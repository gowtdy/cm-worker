#!/usr/bin/env bash
# Run bun install with a fast, resilient heartbeat.
# Avoid --verbose by default (BuildKit clips ~200KiB/s). Opt-in: BUN_INSTALL_VERBOSE=1
#
# After "Resolved, downloaded and extracted" bun is often silent for minutes
# while hardlinking / running scripts — heartbeat must print BEFORE slow finds.
set -euo pipefail

REGISTRY="${BUN_CONFIG_REGISTRY:-${BUN_REGISTRY:-https://registry.npmmirror.com}}"
VERBOSE_FLAG=()
if [[ "${BUN_INSTALL_VERBOSE:-0}" == "1" ]]; then
  VERBOSE_FLAG=(--verbose)
fi

INTERVAL_SECS="${BUN_INSTALL_HEARTBEAT_SECS:-15}"

echo "==> [2/6] bun install --no-optional starting..."
echo "    registry: ${REGISTRY}"
echo "    verbose: ${BUN_INSTALL_VERBOSE:-0} (set BUN_INSTALL_VERBOSE=1 to debug)"
echo "    heartbeat every ${INTERVAL_SECS}s (fast line first; details after)"

LOG=/tmp/bun-install.log
: >"$LOG"
PREV_PKGS_FILE=/tmp/bun-install-prev-pkgs.txt
: >"$PREV_PKGS_FILE"

bun install --no-optional "${VERBOSE_FLAG[@]}" --registry="${REGISTRY}" >"$LOG" 2>&1 &
bun_pid=$!

tail -n +1 -F "$LOG" &
tail_pid=$!

# Announce the quiet post-resolve phase as soon as bun logs it.
(
  set +e
  announced=0
  while kill -0 "$bun_pid" 2>/dev/null; do
    if [[ "$announced" -eq 0 ]] && grep -q 'Resolved, downloaded and extracted' "$LOG" 2>/dev/null; then
      echo "==> [2/6] download/extract done — bun is now linking / running scripts (often quiet; heartbeat continues)"
      announced=1
    fi
    sleep 1
  done
) &
phase_pid=$!

join_csv() {
  awk 'NR>1{printf ", "}{printf "%s", $0} END{print ""}'
}

# Cheap package-dir count (no sort).
cheap_pkg_count() {
  find node_modules/.bun -mindepth 1 -maxdepth 1 -type d ! -name node_modules 2>/dev/null \
    | wc -l | tr -d ' '
}

list_bun_pkg_dirs() {
  find node_modules/.bun -mindepth 1 -maxdepth 1 -type d ! -name node_modules 2>/dev/null \
    | sed 's|.*/||' \
    | sed 's|+|/|g' \
    | sort
}

recent_bun_pkgs() {
  local n="${1:-5}"
  find node_modules/.bun -mindepth 1 -maxdepth 1 -type d ! -name node_modules \
    -printf '%T@\t%f\n' 2>/dev/null \
    | sort -nr \
    | head -n "$n" \
    | cut -f2- \
    | sed 's|+|/|g' \
    | join_csv
}

new_since_prev() {
  local now_file="$1" prev_file="$2"
  [[ -s "$prev_file" ]] || return 0
  comm -13 "$prev_file" "$now_file" | head -n 8 | join_csv
}

proc_summary() {
  local pid="$1"
  if [[ ! -r "/proc/${pid}/stat" ]]; then
    echo "gone"
    return
  fi
  local state utime stime cpu_s rss read_b write_b
  # /proc/pid/stat: field2 is (comm) and may contain spaces — parse after ") ".
  state=$(awk '{ sub(/^[^ ]+ \([^)]*\) /, ""); print $1 }' "/proc/${pid}/stat")
  utime=$(awk '{ sub(/^[^ ]+ \([^)]*\) /, ""); print $12 }' "/proc/${pid}/stat")
  stime=$(awk '{ sub(/^[^ ]+ \([^)]*\) /, ""); print $13 }' "/proc/${pid}/stat")
  utime=${utime:-0}
  stime=${stime:-0}
  utime=${utime//[^0-9]/}
  stime=${stime//[^0-9]/}
  cpu_s=$(( (${utime:-0} + ${stime:-0}) / 100 ))

  rss=$(awk '/^VmRSS:/ {print $2; exit}' "/proc/${pid}/status" 2>/dev/null)
  rss=${rss:-0}
  rss=${rss//[^0-9]/}

  read_b=0
  write_b=0
  if [[ -r "/proc/${pid}/io" ]]; then
    # Exact $1 match — '/write_bytes:/' also hits cancelled_write_bytes.
    read_b=$(awk '$1=="read_bytes:" {print $2; exit}' "/proc/${pid}/io")
    write_b=$(awk '$1=="write_bytes:" {print $2; exit}' "/proc/${pid}/io")
  fi
  read_b=${read_b:-0}
  write_b=${write_b:-0}
  read_b=${read_b//[^0-9]/}
  write_b=${write_b//[^0-9]/}

  echo "state=${state:-?} cpu≈${cpu_s}s rss=$(( ${rss:-0} / 1024 ))MB io_r=$(( ${read_b:-0} / 1048576 ))MB io_w=$(( ${write_b:-0} / 1048576 ))MB"
}

children_summary() {
  local pid="$1" kids
  kids=$(ps --ppid "$pid" -o pid=,etime=,pcpu=,stat=,comm= --no-headers 2>/dev/null | head -n 6 || true)
  if [[ -z "${kids// /}" ]]; then
    echo "-"
    return
  fi
  echo "$kids" | awk '{printf "pid=%s etime=%s cpu=%s%% stat=%s cmd=%s; ", $1,$2,$3,$4,$5} END{print ""}'
}

touched_summary() {
  {
    timeout 3 find node_modules/.bun -mindepth 1 -maxdepth 2 -mmin -2 -type d ! -name node_modules \
      2>/dev/null \
      | sed 's|^node_modules/.bun/||' \
      | sed 's|+|/|g' \
      | head -n 8
  } | join_csv
}

log_tail_summary() {
  grep -v '^[[:space:]]*$' "$LOG" 2>/dev/null | tail -n 2 \
    | sed 's/^/    log| /' || true
}

# Heartbeat must never die on helper failures (set -e in parent).
heartbeat() {
  set +e
  local elapsed=0 prev_pkgs=0 first=1
  local prev_cpu=0 prev_io_r=0 prev_io_w=0
  local tick=0

  echo "==> [2/6] heartbeat armed (pid=${bun_pid}, every ${INTERVAL_SECS}s)"

  while kill -0 "$bun_pid" 2>/dev/null; do
    sleep "$INTERVAL_SECS"
    elapsed=$((elapsed + INTERVAL_SECS))
    kill -0 "$bun_pid" 2>/dev/null || break
    tick=$((tick + 1))

    # --- FAST path: print immediately so Docker always sees liveness ---
    local pkgs cache_entries delta proc cpu_s io_r io_w d_cpu d_ior d_iow kids
    pkgs=$(cheap_pkg_count)
    pkgs=${pkgs:-0}
    cache_entries=$(find /root/.bun/install/cache -mindepth 1 -maxdepth 1 2>/dev/null | wc -l | tr -d ' ')
    cache_entries=${cache_entries:-0}
    delta=$((pkgs - prev_pkgs))
    prev_pkgs=$pkgs

    proc=$(proc_summary "$bun_pid")
    kids=$(children_summary "$bun_pid")

    cpu_s=$(sed -n 's/.*cpu≈\([0-9]*\)s.*/\1/p' <<<"$proc")
    io_r=$(sed -n 's/.*io_r=\([0-9]*\)MB.*/\1/p' <<<"$proc")
    io_w=$(sed -n 's/.*io_w=\([0-9]*\)MB.*/\1/p' <<<"$proc")
    cpu_s=${cpu_s:-0}
    io_r=${io_r:-0}
    io_w=${io_w:-0}
    d_cpu=$((cpu_s - prev_cpu))
    d_ior=$((io_r - prev_io_r))
    d_iow=$((io_w - prev_io_w))
    prev_cpu=$cpu_s
    prev_io_r=$io_r
    prev_io_w=$io_w

    echo "==> [2/6] still running ${elapsed}s | .bun=${pkgs} (+${delta}) cache=${cache_entries} | proc: ${proc} (Δcpu=${d_cpu}s Δio_r=${d_ior}MB Δio_w=${d_iow}MB)"
    echo "    children: ${kids}"
    log_tail_summary

    # --- SLOWER details every other tick (avoid blocking the next beat) ---
    if (( tick % 3 == 0 )); then
      local now_file new_list recent_list new_count touched
      now_file=/tmp/bun-install-now-pkgs.txt
      list_bun_pkg_dirs >"$now_file" 2>/dev/null
      recent_list=$(recent_bun_pkgs 5)
      [[ -n "$recent_list" ]] || recent_list="-"

      if [[ "$first" -eq 1 ]]; then
        new_list="new: (baseline ${pkgs} pkgs)"
        first=0
      else
        new_list=$(new_since_prev "$now_file" "$PREV_PKGS_FILE")
        new_count=$(comm -13 "$PREV_PKGS_FILE" "$now_file" 2>/dev/null | wc -l | tr -d ' ')
        new_count=${new_count:-0}
        if [[ "$new_count" -eq 0 ]]; then
          new_list="new(0): -"
        elif [[ -z "$new_list" ]]; then
          new_list="new(${new_count}): (${new_count} pkgs)"
        elif [[ "$new_count" -gt 8 ]]; then
          new_list="new(${new_count}): ${new_list}, …(+$((new_count - 8)) more)"
        else
          new_list="new(${new_count}): ${new_list}"
        fi
      fi
      cp "$now_file" "$PREV_PKGS_FILE" 2>/dev/null

      touched=$(touched_summary)
      [[ -n "$touched" ]] || touched="-"

      echo "    ${new_list}"
      echo "    recent: ${recent_list}"
      echo "    touched(~2m): ${touched}"
    fi
  done
}

heartbeat &
hb_pid=$!

wait "$bun_pid"
status=$?
kill "$tail_pid" "$hb_pid" "$phase_pid" 2>/dev/null || true
wait "$tail_pid" "$hb_pid" "$phase_pid" 2>/dev/null || true

if [[ "$status" -ne 0 ]]; then
  echo "==> [2/6] bun install failed (exit ${status}); last 40 log lines:" >&2
  tail -n 40 "$LOG" >&2 || true
fi
exit "$status"
