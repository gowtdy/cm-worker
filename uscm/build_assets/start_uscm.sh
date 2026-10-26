#!/bin/sh

#set -x

PORT=${APP_PORT:-3100}
WORKERS=${WORKERS:-30}

uvicorn uscm_server:app --host 0.0.0.0 --port "$PORT" --workers "${WORKERS}"
