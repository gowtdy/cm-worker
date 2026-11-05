#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
"$SCRIPT_DIR/../../common/sync_cmwk_common.sh" "$SCRIPT_DIR"
IMG_TAG="$(cat "$SCRIPT_DIR/version.txt")"
docker build -t whisper-transcribe-worker:"${IMG_TAG}" -f Dockerfile \
  --build-arg TRANS_ENGINE=whisper --build-arg IMG_TAG="${IMG_TAG}" .
