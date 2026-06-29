"""Chatbot Parlamento italiano — Flask + Mistral Large + MCP (italianparliament-mcp).

Loop agentico con function calling: Mistral decide quali tool MCP chiamare,
il backend li esegue via stdio e ricicla i risultati finché il modello produce
la risposta finale. Lo streaming SSE porta al browser sia gli stati ("sto
consultando le votazioni…") sia i token della risposta.
"""

import json
import logging
import os
import threading
import time

from flask import Flask, Response, render_template, request, stream_with_context
from mistralai import Mistral

from mcp_client import MCPStdioClient

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

app = Flask(__name__)

MODEL = os.environ.get("MISTRAL_MODEL", "mistral-large-latest")
MAX_STEPS = int(os.environ.get("MAX_STEPS", "6"))

client = Mistral(api_key=os.environ["MISTRAL_API_KEY"])

mcp = MCPStdioClient()
TOOLS = []
_mcp_error = None

def _init_mcp():
    global TOOLS, _mcp_error
    try:
        log.info("Avvio server MCP...")
        mcp.start()
        TOOLS = mcp.tool_schemas()
        log.info(f"Server MCP pronto — {len(TOOLS)} tool disponibili.")
    except Exception as e:
        _mcp_error = str(e)
        log.error(f"Errore avvio MCP: {e}")

threading.Thread(target=_init_mcp, daemon=True).start()

SYSTEM_PROMPT = """Sei un assistente che fornisce dati sul Parlamento italiano attingendo ESCLUSIVAMENTE agli strumenti di ricerca disponibili. Rispondi sempre in italiano.

REGOLA ASSOLUTA — NESSUNA ECCEZIONE:
Ogni affermazione fattuale (nomi, ruoli, date, numeri di voto, testi di legge, appartenenze a gruppi, cariche) DEVE provenire dal risultato di un tool chiamato in questa conversazione.
NON usare MAI la tua conoscenza preaddestrata per affermare fatti: potrebbe essere obsoleta, errata o riferita a legislature precedenti.
Se un dato non è nei risultati dei tool, scrivi esattamente: "Non ho trovato questo dato nei dati aperti del Parlamento italiano."
Non fare ipotesi, non completare informazioni mancanti, non parafrasare con aggiunte tue.

REGOLE OPERATIVE:
1. Chiama sempre almeno un tool prima di rispondere a qualsiasi domanda fattuale.
2. Cita la fonte per ogni dato: "(Camera, tool: search)" o "(Senato, tool: senato-votes)" ecc.
3. Non inventare MAI URI. Ottienili con `search`, `bills`, `votes` prima di chiamare tool di dettaglio.
4. La legislatura corrente è la 19ª. Specificala sempre nei risultati.
5. Se il tool restituisce 0 risultati, dillo: "La ricerca non ha prodotto risultati."
6. Se il tool restituisce un messaggio che inizia con "Error:", riportalo TESTUALMENTE nella risposta tra backtick, senza riformularlo né interpretarlo.

WORKFLOW:
- Persona → `search` (ottieni URI) → `deputy`/`senator`/`person-career`
- Votazione di qualcuno → `votes`/`senato-votes` (ottieni URI votazione) → `vote-detail`/`senato-vote-detail`
- Attività/classifiche → `rank`, `aic`, `group-rank`
- Dati non coperti → `sparql` (endpoint `camera` o `senato`)

FORMATO RISPOSTA:
Usa tabelle markdown per liste di dati. Alla fine di ogni risposta aggiungi una riga:
> Fonte: [nome tool usati] — Dati aperti Camera/Senato, legislatura 19ª."""


def sse(event):
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def run_agent(user_message, history):
    # Attende al massimo 60s che il server MCP sia pronto
    for _ in range(60):
        if TOOLS:
            break
        time.sleep(1)
    else:
        yield sse({"type": "error", "message": "Server MCP non disponibile. Riprova tra qualche secondo."})
        return

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(history)  # turni precedenti: solo {role: user|assistant, content}
    messages.append({"role": "user", "content": user_message})

    for step in range(MAX_STEPS):
        content_parts = []
        calls = {}   # key -> {id, name, args}
        order = []

        # Primo turno: forza almeno una chiamata tool prima di rispondere.
        # Turni successivi: "auto" (il modello può rispondere direttamente coi dati già raccolti).
        tool_choice = "required" if step == 0 else "auto"

        stream = client.chat.stream(
            model=MODEL,
            messages=messages,
            tools=TOOLS,
            tool_choice=tool_choice,
            temperature=0,
        )
        for chunk in stream:
            delta = chunk.data.choices[0].delta
            if getattr(delta, "content", None):
                content_parts.append(delta.content)
                yield sse({"type": "token", "text": delta.content})
            for tc in (getattr(delta, "tool_calls", None) or []):
                idx = getattr(tc, "index", None)
                key = idx if idx is not None else len(order)
                if key not in calls:
                    calls[key] = {"id": None, "name": None, "args": ""}
                    order.append(key)
                slot = calls[key]
                if getattr(tc, "id", None):
                    slot["id"] = tc.id
                fn = getattr(tc, "function", None)
                if fn:
                    if getattr(fn, "name", None):
                        slot["name"] = fn.name
                    arg = getattr(fn, "arguments", None)
                    if arg:
                        slot["args"] += arg if isinstance(arg, str) else json.dumps(arg)

        if not calls:
            yield sse({"type": "done"})
            return

        # Messaggio assistant che "rivendica" le tool call (richiesto dall'API).
        tool_calls = []
        for key in order:
            s = calls[key]
            tool_calls.append({
                "id": s["id"] or f"call_{step}_{key}",
                "type": "function",
                "function": {"name": s["name"], "arguments": s["args"] or "{}"},
            })
        messages.append({
            "role": "assistant",
            "content": "".join(content_parts),
            "tool_calls": tool_calls,
        })

        # Esecuzione dei tool + notifica di stato al browser.
        for tc in tool_calls:
            name = tc["function"]["name"]
            try:
                args = json.loads(tc["function"]["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {}
            yield sse({"type": "tool", "name": name, "args": args})
            result = mcp.call_tool(name, args)
            # Emette il risultato (o errore) del tool nel SSE — visibile in console browser.
            is_error = result.startswith("Error:")
            yield sse({"type": "tool_result", "name": name, "error": is_error,
                       "preview": result[:300]})
            log.info(f"tool={name} args={args} result_preview={result[:200]}")
            messages.append({
                "role": "tool",
                "name": name,
                "tool_call_id": tc["id"],
                "content": result,
            })

    yield sse({"type": "token",
               "text": "\n\n_(Limite di passi raggiunto: la richiesta è troppo complessa, prova a restringerla.)_"})
    yield sse({"type": "done"})


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json(force=True) or {}
    user_message = (data.get("message") or "").strip()
    history = data.get("history") or []
    if not user_message:
        return Response(sse({"type": "error", "message": "Messaggio vuoto."}),
                        mimetype="text/event-stream")

    @stream_with_context
    def generate():
        try:
            yield from run_agent(user_message, history)
        except Exception as e:  # noqa: BLE001 — qualsiasi errore va riportato al client
            yield sse({"type": "error", "message": str(e)})

    resp = Response(generate(), mimetype="text/event-stream")
    # Anti-buffering (critico su Railway/reverse-proxy) — vedi chatbot precedente.
    resp.headers["X-Accel-Buffering"] = "no"
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["Connection"] = "keep-alive"
    return resp


@app.route("/health")
def health():
    return {"status": "ok", "tools": len(TOOLS), "mcp_ready": bool(TOOLS), "mcp_error": _mcp_error}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
