#!/bin/sh

#set -x

# 确保 log 目录存在并设置正确权限
mkdir -p /app/log
chmod 777 /app/log

PORT=${APP_PORT:-3100}
WORKERS=${WORKERS:-30}

#echo "Starting server on port $PORT..."

#nohup python sounds_effect_server.py --port 2000 2>&1 &
#python uscm_server.py --port "$PORT"

uvicorn uscm_server:app --host 0.0.0.0 --port "$PORT" --workers "${WORKERS}"
