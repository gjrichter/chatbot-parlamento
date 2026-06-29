# Chatbot Parlamento Italiano

Chatbot agentico per i dati aperti del Parlamento italiano (Camera + Senato).
Stack: Flask · Mistral Large · italianparliament-mcp · Gunicorn · Railway.

## Come funziona

Il backend esegue un loop agentico: Mistral decide quali strumenti MCP chiamare,
il backend li esegue via subprocess stdio e ricicla i risultati finché il modello
produce la risposta finale. Il tutto arriva al browser via SSE (streaming).

Fonte dati: [italianparliament-mcp](https://github.com/aborruso/italianparliament-mcp)
(~16 tool: ricerca persone, deputati, senatori, votazioni, disegni di legge, SPARQL).

## Aggiornamento dei dati MCP

Il pacchetto `@aborruso/italianparliament-mcp` viene installato al momento del
build Docker. **Si aggiorna automaticamente all'ultima versione npm ad ogni deploy
su Railway** (quando viene fatto un push di codice).

Se l'autore pubblica una versione nuova e vuoi aggiornarla senza modifiche al
codice: Railway Dashboard → servizio → Deploy → **Redeploy**.

## Sviluppo locale

```bash
export MISTRAL_API_KEY=sk-...
export PORT=5001          # porta libera (5000 occupata da AirPlay su macOS)
pip install -r requirements.txt
npm install -g @aborruso/italianparliament-mcp
python app.py
```

Oppure con gunicorn (come in produzione):

```bash
gunicorn app:app -c gunicorn.conf.py
```

## Deploy su Railway

1. Builder: **Dockerfile** (non Nixpacks)
2. Custom Start Command: **vuoto** (lasciare il campo vuoto — il CMD nel Dockerfile è sufficiente)
3. Variabile d'ambiente: `MISTRAL_API_KEY`

> **Nota**: un Start Command personalizzato su Railway sovrascrive il `CMD` del
> Dockerfile. Se il servizio crasha con `Error: '$PORT' is not a valid port number`,
> verificare che il campo Start Command sia vuoto nelle impostazioni.

## Configurazione

| Variabile | Default | Descrizione |
|-----------|---------|-------------|
| `MISTRAL_API_KEY` | — | **Obbligatoria** |
| `MISTRAL_MODEL` | `mistral-large-latest` | Modello Mistral |
| `MAX_STEPS` | `6` | Massimo passi agentico per risposta |
| `MCP_SERVER_CMD` | auto-detect | Override del comando MCP (es. path assoluto) |
| `PORT` | `8080` | Porta HTTP |
