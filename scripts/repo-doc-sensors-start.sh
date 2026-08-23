#!/bin/bash
set -euo pipefail

CONFIG_DIR="$HOME/.config/personal-koi"
STATE_DIR="$CONFIG_DIR/repo-doc-sensors"
LOG_DIR="$STATE_DIR/logs"
KOI_PROCESSOR="$HOME/projects/koi-processor-runtime"
VENV="$HOME/venvs/koi-server"

SCAN_INTERVAL="${REPO_DOC_SENSOR_SCAN_INTERVAL:-300}"
RECONCILE_INTERVAL="${REPO_DOC_SENSOR_RECONCILE_INTERVAL:-21600}"
START_DELAY="${REPO_DOC_SENSOR_START_DELAY:-12}"

# Supervisor behaviour: respawn individual sensors instead of killing the whole
# stack when one dies. A sensor that dies within MIN_UPTIME seconds is treated
# as a fast crash and its backoff doubles (up to MAX_BACKOFF) before respawning.
# A sensor that dies after running stably resets its backoff to 1 and respawns
# immediately.
MIN_UPTIME="${REPO_DOC_SENSOR_MIN_UPTIME:-60}"
MAX_BACKOFF="${REPO_DOC_SENSOR_MAX_BACKOFF:-300}"

REPOS=(
  "$HOME/projects/darren-workflow:darren-workflow"
  "$HOME/projects/spore:spore"
  "$HOME/projects/sheaf-explorer/src/knowledge:sheaf-explorer"
  "$HOME/projects/intelligence-commons:intelligence-commons"
  "$HOME/projects/salish-sea-dreaming:salish-sea-dreaming"
  "$HOME/projects/BioregionKnwoledgeCommons/BioregionalKnowledgeCommoning:bkc"
  "$HOME/projects/poietic-match:poietic-match"
)

mkdir -p "$STATE_DIR" "$LOG_DIR"

if [[ ! -x "$VENV/bin/python3" ]]; then
  echo "ERROR: python not found at $VENV/bin/python3" >&2
  exit 1
fi

if [[ ! -f "$KOI_PROCESSOR/scripts/doc_scanner.py" ]]; then
  echo "ERROR: doc_scanner.py not found at $KOI_PROCESSOR/scripts/doc_scanner.py" >&2
  exit 1
fi

set +u
set -a
[[ -f "$KOI_PROCESSOR/.env" ]] && source "$KOI_PROCESSOR/.env"
[[ -f "$KOI_PROCESSOR/config/personal.env" ]] && source "$KOI_PROCESSOR/config/personal.env"
set +a
set -u

export POSTGRES_URL="${POSTGRES_URL:-postgresql://${USER}:@localhost:5432/personal_koi}"
export PYTHONUNBUFFERED=1

# Parallel arrays indexed by sensor slot
declare -a REPO_PATHS
declare -a REPO_NAMES
declare -a PIDS
declare -a START_TIMES
declare -a BACKOFFS
STOPPING=0

cleanup() {
  if [[ "$STOPPING" -eq 1 ]]; then
    return
  fi
  STOPPING=1
  for pid in "${PIDS[@]:-}"; do
    if [[ -n "${pid:-}" ]] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
    fi
  done
  for pid in "${PIDS[@]:-}"; do
    if [[ -n "${pid:-}" ]]; then
      wait "$pid" 2>/dev/null || true
    fi
  done
}

trap cleanup INT TERM EXIT

spawn_sensor() {
  local idx="$1"
  local repo_path="${REPO_PATHS[$idx]}"
  local repo_name="${REPO_NAMES[$idx]}"
  local log_file="$LOG_DIR/${repo_name}.log"

  if [[ ! -d "$repo_path" ]]; then
    echo "ERROR: repo path not found: $repo_path" >&2
    return 1
  fi

  echo "[repo-doc-sensors] starting $repo_name ($repo_path)"
  "$VENV/bin/python3" "$KOI_PROCESSOR/scripts/doc_scanner.py" \
    "$repo_path" \
    --repo-name "$repo_name" \
    --doc-id-only \
    --watch \
    --scan-interval "$SCAN_INTERVAL" \
    --reconcile-interval "$RECONCILE_INTERVAL" \
    >> "$log_file" 2>&1 &

  PIDS[$idx]="$!"
  START_TIMES[$idx]="$(date +%s)"
}

# Initial spawn, staggered to avoid dogpiling KOI on startup
for i in "${!REPOS[@]}"; do
  spec="${REPOS[$i]}"
  REPO_PATHS[$i]="${spec%%:*}"
  REPO_NAMES[$i]="${spec##*:}"
  BACKOFFS[$i]=1

  spawn_sensor "$i"

  if [[ "$i" -lt $((${#REPOS[@]} - 1)) && "$START_DELAY" -gt 0 ]]; then
    echo "[repo-doc-sensors] sleeping ${START_DELAY}s before next sensor"
    sleep "$START_DELAY"
  fi
done

echo "[repo-doc-sensors] running ${#PIDS[@]} sensors with respawn-on-crash supervisor"

# Supervisor loop: respawn individual dead sensors instead of killing the stack.
while true; do
  if [[ "$STOPPING" -eq 1 ]]; then
    break
  fi

  for i in "${!REPOS[@]}"; do
    pid="${PIDS[$i]}"
    name="${REPO_NAMES[$i]}"

    if kill -0 "$pid" 2>/dev/null; then
      continue
    fi

    # Process is gone — reap it and record exit status
    status=0
    wait "$pid" 2>/dev/null || status=$?

    if [[ "$STOPPING" -eq 1 ]]; then
      break 2
    fi

    now="$(date +%s)"
    uptime=$((now - START_TIMES[i]))

    if [[ "$uptime" -lt "$MIN_UPTIME" ]]; then
      # Fast crash — exponential backoff
      BACKOFFS[$i]=$((BACKOFFS[i] * 2))
      if [[ "${BACKOFFS[$i]}" -gt "$MAX_BACKOFF" ]]; then
        BACKOFFS[$i]="$MAX_BACKOFF"
      fi
      echo "[repo-doc-sensors] sensor '$name' crashed after ${uptime}s (status $status); backing off ${BACKOFFS[$i]}s before respawn" >&2
      sleep "${BACKOFFS[$i]}"
    else
      # Stable sensor died — reset backoff, respawn immediately
      BACKOFFS[$i]=1
      echo "[repo-doc-sensors] sensor '$name' exited after ${uptime}s (status $status); respawning" >&2
    fi

    if [[ "$STOPPING" -eq 1 ]]; then
      break 2
    fi

    spawn_sensor "$i" || true
  done

  sleep 5
done
