#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
"$SCRIPT_DIR/../common/sync_cmwk_common.sh" "$SCRIPT_DIR"
docker build -t uscm-image:0.1 -f Dockerfile .
