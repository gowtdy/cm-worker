#!/usr/bin/env bash
set -e
export AIGC_UID=${AIGC_UID:-$(id -u aigc 2>/dev/null || echo 1001)}
export AIGC_GID=${AIGC_GID:-$(id -g aigc 2>/dev/null || echo 1001)}
docker compose -f docker-compose-whisper.yml up -d
