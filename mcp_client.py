"""Client MCP stdio sincrono per il server `italianparliament-mcp`.

Lancia il server MCP come sottoprocesso Node e parla il protocollo MCP
(JSON-RPC 2.0 newline-delimited su stdin/stdout). È volutamente sincrono:
sotto Gunicorn+gevent il monkey-patching rende le pipe del sottoprocesso
cooperative (le readline cedono il controllo agli altri greenlet), evitando
il conflitto fra l'event loop asyncio dell'SDK MCP ufficiale e gevent.

Espone due helper usabili dai worker Flask/gevent:
  - tool_schemas(): i tool whitelistati in formato function-calling Mistral
  - call_tool(name, args): esegue un tool e restituisce il testo del risultato
"""

import json
import os
import subprocess
import threading

# Sottoinsieme curato di tool esposto all'LLM (su 36 totali del server).
TOOL_WHITELIST = {
    # persone
    "search", "deputy", "senator", "person-career",
    # camera
    "bills", "bill", "votes", "vote-detail", "aic",
    # senato
    "senato-votes",
    # organizzazione
    "groups", "group-members",
    # contesto / analisi
    "governments", "gov-members", "rank",
    # escape hatch per query non coperte dai tool dedicati
    "sparql",
}

# Comando che avvia il server MCP via stdio.
# In produzione (Docker) il pacchetto è installato globalmente: "italianparliament-mcp".
# In sviluppo locale si può usare npx: MCP_SERVER_CMD="npx -y @aborruso/italianparliament-mcp"
def _default_server_command():
    import shutil
    if shutil.which("italianparliament-mcp"):
        return ["italianparliament-mcp"]
    return ["npx", "-y", "@aborruso/italianparliament-mcp"]

SERVER_COMMAND = (
    os.environ["MCP_SERVER_CMD"].split()
    if "MCP_SERVER_CMD" in os.environ
    else _default_server_command()
)

PROTOCOL_VERSION = "2024-11-05"
RESULT_MAX_CHARS = 20000  # tappo anti-flooding del contesto su dump SPARQL grandi


class MCPError(RuntimeError):
    pass


class MCPStdioClient:
    def __init__(self, command=None):
        self._command = command or SERVER_COMMAND
        self._proc = None
        self._lock = threading.Lock()  # serializza le richieste sul singolo sottoprocesso
        self._next = 0
        self._tools_raw = []

    # --- ciclo di vita -------------------------------------------------

    def start(self):
        self._proc = subprocess.Popen(
            self._command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,  # i log del server vanno su stderr: stdout resta JSON-RPC pulito
            text=True,
            bufsize=1,  # line-buffered
        )
        self._request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "chatbot-parlamento", "version": "0.1.0"},
        })
        self._notify("notifications/initialized", {})
        listed = self._request("tools/list", {})
        self._tools_raw = listed.get("tools", [])
        if not self._tools_raw:
            raise MCPError("il server MCP non ha restituito alcun tool")

    # --- trasporto JSON-RPC -------------------------------------------

    def _send(self, obj):
        self._proc.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self._proc.stdin.flush()

    def _notify(self, method, params):
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _request(self, method, params):
        self._next += 1
        rid = self._next
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        # Legge finché non arriva la risposta con l'id atteso; ignora notifiche
        # e righe non-JSON (eventuali log finiti su stdout).
        while True:
            line = self._proc.stdout.readline()
            if not line:
                raise MCPError("connessione col server MCP chiusa")
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("id") == rid:
                if "error" in msg:
                    raise MCPError(str(msg["error"]))
                return msg.get("result", {})

    # --- API per il chatbot -------------------------------------------

    def tool_schemas(self):
        """Tool whitelistati in formato function-calling Mistral/OpenAI."""
        out = []
        for t in self._tools_raw:
            name = t.get("name")
            if name not in TOOL_WHITELIST:
                continue
            out.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": t.get("description", ""),
                    "parameters": t.get("inputSchema") or {"type": "object", "properties": {}},
                },
            })
        return out

    def call_tool(self, name, arguments):
        """Esegue un tool MCP, restituisce il testo del risultato (o un Error: ...)."""
        if name not in TOOL_WHITELIST:
            return f"Error: tool '{name}' non disponibile."
        with self._lock:  # una richiesta in volo per volta sul sottoprocesso
            try:
                result = self._request("tools/call", {"name": name, "arguments": arguments})
            except MCPError as e:
                return f"Error: {e}"
        parts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
        text = "\n".join(p for p in parts if p) or "No results."
        if result.get("isError"):
            return f"Error: {text}"
        return text[:RESULT_MAX_CHARS]
