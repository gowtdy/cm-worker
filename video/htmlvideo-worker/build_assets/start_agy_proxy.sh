#!/bin/bash

set -e

# V2Ray HTTP Proxy
PROXY="http://127.0.0.1:3128"

# Antigravity application
APP="agy"

# Check proxy availability
if ! curl -sS --proxy "$PROXY" \
  --connect-timeout 5 \
  -I https://accounts.google.com >/dev/null; then
    echo "ERROR: V2Ray proxy is unavailable: $PROXY"
    exit 1
fi

# Launch Antigravity with proxy environment variables
open -na "$APP" \
  --env "HTTP_PROXY=$PROXY" \
  --env "HTTPS_PROXY=$PROXY" \
  --env "ALL_PROXY=$PROXY" \
  --env "http_proxy=$PROXY" \
  --env "https_proxy=$PROXY" \
  --env "all_proxy=$PROXY" \
  --env "NO_PROXY=localhost,127.0.0.1,::1" \
  --env "no_proxy=localhost,127.0.0.1,::1" \
  --args \
  "--proxy-server=$PROXY" \
  '--proxy-bypass-list=localhost;127.0.0.1;[::1]'

echo "Antigravity launched with V2Ray proxy."
