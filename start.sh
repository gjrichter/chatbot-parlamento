#!/bin/sh
exec gunicorn app:app --worker-class gevent --workers 1 --timeout 180 --bind "0.0.0.0:${PORT:-8080}"
