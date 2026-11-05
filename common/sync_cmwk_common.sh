#!/usr/bin/env bash
# Sync repo-root common/cmwk_common into a service's build_assets/cmwk_common.
# Usage: sync_cmwk_common.sh <service_dir>
set -euo pipefail

SERVICE_DIR="$(cd "${1:?service_dir required}" && pwd)"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$REPO_ROOT/common/cmwk_common/"
DST="$SERVICE_DIR/build_assets/cmwk_common/"

if [[ ! -d "$SRC" ]]; then
  echo "cmwk_common source missing: $SRC" >&2
  exit 1
fi

mkdir -p "$DST"
rsync -a --delete "$SRC" "$DST"
echo "synced cmwk_common -> $DST"
