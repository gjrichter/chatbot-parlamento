import os

bind = f"0.0.0.0:{os.environ.get('PORT', '8080')}"
worker_class = "gthread"
workers = 1
threads = 8       # richieste concorrenti — ogni SSE tiene un thread per la durata dello stream
timeout = 180
