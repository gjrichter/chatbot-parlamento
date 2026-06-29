import os

bind = f"0.0.0.0:{os.environ.get('PORT', '8080')}"
worker_class = "gevent"
workers = 1
timeout = 180
