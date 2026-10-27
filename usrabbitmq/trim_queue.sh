#!/usr/bin/env bash
# Trim a RabbitMQ queue: drop oldest ready messages, keep the newest N.
# Default: container usrabbitmq, queue us_indextts_queue, keep 50.
#
# Usage:
#   ./trim_queue.sh                          # keep last 50 of us_indextts_queue
#   ./trim_queue.sh us_indextts_queue 50
#   ./trim_queue.sh us_indextts_queue 50 --yes
#   KEEP=100 QUEUE=gpu_tts_tasks ./trim_queue.sh --yes
#
# Env overrides:
#   CONTAINER  RabbitMQ container name (default: aivoicemq)
#   QUEUE      Queue name
#   KEEP       Number of newest ready messages to keep (default: 50)
#   BATCH      Messages to drop per Management API call (default: 200)
#   MGMT_HOST  Management API host (default: 127.0.0.1)
#   MGMT_PORT  Management API port (default: 15672)
#   MQ_USER / MQ_PASS  Credentials (default: read from container env, else aigc/password)

set -euo pipefail

CONTAINER="${CONTAINER:-usaivoicemq}"
QUEUE="${QUEUE:-us_indextts_queue}"
KEEP="${KEEP:-50}"
BATCH="${BATCH:-200}"
MGMT_HOST="${MGMT_HOST:-127.0.0.1}"
MGMT_PORT="${MGMT_PORT:-15672}"
YES=0

ARGS=()
for arg in "$@"; do
  case "$arg" in
    -y|--yes) YES=1 ;;
    -h|--help)
      sed -n '2,20p' "$0"
      exit 0
      ;;
    *) ARGS+=("$arg") ;;
  esac
done

if [[ ${#ARGS[@]} -ge 1 ]]; then
  QUEUE="${ARGS[0]}"
fi
if [[ ${#ARGS[@]} -ge 2 ]]; then
  KEEP="${ARGS[1]}"
fi

if ! [[ "$KEEP" =~ ^[0-9]+$ ]] || [[ "$KEEP" -lt 0 ]]; then
  echo "error: KEEP must be a non-negative integer, got: $KEEP" >&2
  exit 1
fi

if ! docker inspect "$CONTAINER" >/dev/null 2>&1; then
  echo "error: container '$CONTAINER' not found" >&2
  exit 1
fi

if [[ -z "${MQ_USER:-}" ]]; then
  MQ_USER="$(docker exec "$CONTAINER" printenv RABBITMQ_DEFAULT_USER 2>/dev/null || true)"
fi
if [[ -z "${MQ_PASS:-}" ]]; then
  MQ_PASS="$(docker exec "$CONTAINER" printenv RABBITMQ_DEFAULT_PASS 2>/dev/null || true)"
fi
MQ_USER="${MQ_USER:-aigc}"
MQ_PASS="${MQ_PASS:-password}"

queue_stats() {
  # name messages messages_ready messages_unacknowledged
  docker exec "$CONTAINER" rabbitmqctl list_queues name messages messages_ready messages_unacknowledged -q \
    | awk -v q="$QUEUE" '$1 == q { print $2, $3, $4; found=1 } END { if (!found) exit 2 }'
}

if ! STATS="$(queue_stats)"; then
  echo "error: queue '$QUEUE' not found" >&2
  exit 1
fi
read -r MESSAGES READY UNACKED <<<"$STATS"

echo "container : $CONTAINER"
echo "queue     : $QUEUE"
echo "messages  : $MESSAGES  (ready=$READY unacked=$UNACKED)"
echo "keep      : $KEEP newest ready message(s)"

if [[ "$READY" -le "$KEEP" ]]; then
  echo "nothing to drop (ready=$READY <= keep=$KEEP)"
  exit 0
fi

DROP=$((READY - KEEP))
echo "will drop : $DROP oldest ready message(s)"

if [[ "$YES" -ne 1 ]]; then
  read -r -p "Continue? [y/N] " reply
  case "$reply" in
    y|Y|yes|YES) ;;
    *) echo "aborted"; exit 1 ;;
  esac
fi

MGMT_URL="http://${MGMT_HOST}:${MGMT_PORT}/api/queues/%2F/${QUEUE}/get"
remaining="$DROP"
dropped=0

while [[ "$remaining" -gt 0 ]]; do
  n="$BATCH"
  if [[ "$remaining" -lt "$BATCH" ]]; then
    n="$remaining"
  fi

  resp="$(curl -sS -u "${MQ_USER}:${MQ_PASS}" -X POST "$MGMT_URL" \
    -H 'content-type: application/json' \
    -d "{\"count\":${n},\"ackmode\":\"ack_requeue_false\",\"encoding\":\"auto\"}")"

  # Response is a JSON array of messages actually retrieved.
  got="$(python3 -c 'import json,sys; d=json.load(sys.stdin); print(len(d) if isinstance(d,list) else 0)' <<<"$resp")"
  if [[ "$got" -eq 0 ]]; then
    echo "warning: API returned 0 messages (queue may be empty or API rejected); stopping"
    break
  fi

  dropped=$((dropped + got))
  remaining=$((remaining - got))
  printf '\rdropped %d / %d' "$dropped" "$DROP"
done
echo

if STATS="$(queue_stats)"; then
  read -r MESSAGES READY UNACKED <<<"$STATS"
  echo "done. messages=$MESSAGES ready=$READY unacked=$UNACKED"
else
  echo "done. (queue disappeared or empty)"
fi
