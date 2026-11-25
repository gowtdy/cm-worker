#!/usr/bin/env bash
# 在 Ubuntu Docker（network_mode: host）中通过本地 HTTP 代理启动 agy CLI。
# 依赖宿主机 V2Ray HTTP 入站监听 127.0.0.1:3128（或通过 AGY_HTTP_PROXY 覆盖）。
set -euo pipefail

PROXY="${AGY_HTTP_PROXY:-${HTTP_PROXY:-http://127.0.0.1:3128}}"
NO_PROXY_LIST="${NO_PROXY:-localhost,127.0.0.1,::1,host.docker.internal}"
AGY_BIN="${AGY_BIN:-agy}"
SKIP_PROXY_CHECK="${AGY_SKIP_PROXY_CHECK:-0}"

if ! command -v "$AGY_BIN" >/dev/null 2>&1; then
  echo "ERROR: agy not found in PATH (AGY_BIN=$AGY_BIN)" >&2
  exit 127
fi

if [[ "$SKIP_PROXY_CHECK" != "1" ]]; then
  if ! command -v curl >/dev/null 2>&1; then
    echo "ERROR: curl is required for proxy check (or set AGY_SKIP_PROXY_CHECK=1)" >&2
    exit 1
  fi
  if ! curl -sS --proxy "$PROXY" \
    --connect-timeout 5 \
    --max-time 15 \
    -I https://accounts.google.com >/dev/null; then
    echo "ERROR: HTTP proxy unavailable: $PROXY" >&2
    echo "HINT: ensure V2Ray HTTP inbound is listening; container uses host network so 127.0.0.1 is the host." >&2
    exit 1
  fi
fi

export HTTP_PROXY="$PROXY"
export HTTPS_PROXY="$PROXY"
export http_proxy="$PROXY"
export https_proxy="$PROXY"
export NO_PROXY="$NO_PROXY_LIST"
export no_proxy="$NO_PROXY_LIST"
# Node 20+/undici 默认不读 HTTP_PROXY，agy 的网络请求需要显式打开
export NODE_USE_ENV_PROXY=1

echo "Starting agy via proxy: $PROXY"
exec "$AGY_BIN" "$@"
