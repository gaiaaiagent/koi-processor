#!/bin/zsh
# recall-trigger-eval.sh — Tier-2 trigger evaluator (Step 7).
#
# Per plan §Rollback "Metrics computation":
#   - Reads last 50 events from ~/.koi/logs/recall-metrics.jsonl
#   - Computes p95 latency_ms_total → trigger if >5000ms
#   - Reads last 100 events; computes error rate → trigger if >5%
#   - Checks Graphiti health via redis-cli PING (5s timeout)
#       → trigger if 5 consecutive 5-min checks fail
#   - On any trigger: append to ~/.koi/recall-trigger-log.jsonl
#       AND set RECALL_ROUTING_ENABLED=false in env file (auto-revert)
#
# Cron: */5 * * * *
#
# Idempotent. Trigger fires are recorded individually; auto-revert env-file
# write is idempotent (re-writing same key is no-op when key already false).

set -e

METRICS_FILE="$HOME/.koi/logs/recall-metrics.jsonl"
TRIGGER_LOG="$HOME/.koi/recall-trigger-log.jsonl"
HEALTH_FILE="$HOME/.koi/graphiti-health.txt"
ENV_FILE="$HOME/.config/personal-koi/personal.env"

LATENCY_P95_THRESHOLD_MS=5000
ERROR_RATE_THRESHOLD=0.05      # 5%
GRAPHITI_FAILURE_RUN_THRESHOLD=5  # 5 consecutive 5-min checks

mkdir -p "$(dirname "$TRIGGER_LOG")"
mkdir -p "$(dirname "$HEALTH_FILE")"

NOW_ISO="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

emit_trigger() {
    local trigger_type="$1"
    local metric_value="$2"
    local action="$3"
    printf '{"ts":"%s","trigger_type":"%s","metric_value":%s,"action":"%s"}\n' \
        "$NOW_ISO" "$trigger_type" "$metric_value" "$action" >> "$TRIGGER_LOG"
}

set_routing_disabled() {
    if [[ ! -f "$ENV_FILE" ]]; then
        return 0
    fi
    if grep -q '^RECALL_ROUTING_ENABLED=false' "$ENV_FILE" 2>/dev/null; then
        return 0  # already disabled — idempotent
    fi
    if grep -q '^RECALL_ROUTING_ENABLED=' "$ENV_FILE" 2>/dev/null; then
        # Replace existing value
        sed -i.bak 's/^RECALL_ROUTING_ENABLED=.*/RECALL_ROUTING_ENABLED=false/' "$ENV_FILE"
    else
        printf '\nRECALL_ROUTING_ENABLED=false\n' >> "$ENV_FILE"
    fi
}

# --- Latency p95 over last 50 events ---
if [[ -f "$METRICS_FILE" ]]; then
    P95=$(tail -n 50 "$METRICS_FILE" 2>/dev/null \
        | python3 -c '
import sys, json
lats = []
for line in sys.stdin:
    line = line.strip()
    if not line: continue
    try:
        d = json.loads(line)
        v = d.get("latency_ms_total")
        if isinstance(v, (int, float)):
            lats.append(v)
    except Exception:
        pass
if not lats:
    print(0)
else:
    lats.sort()
    idx = max(0, int(len(lats) * 0.95) - 1)
    print(lats[idx])
' || echo 0)
    if (( $(printf '%.0f' "$P95") > LATENCY_P95_THRESHOLD_MS )); then
        emit_trigger "latency_p95" "$P95" "set_routing_disabled"
        set_routing_disabled
    fi
fi

# --- Error rate over last 100 events ---
if [[ -f "$METRICS_FILE" ]]; then
    ERR_RATE=$(tail -n 100 "$METRICS_FILE" 2>/dev/null \
        | python3 -c '
import sys, json
total = 0
errs = 0
for line in sys.stdin:
    line = line.strip()
    if not line: continue
    try:
        d = json.loads(line)
        total += 1
        if d.get("error_code") is not None:
            errs += 1
    except Exception:
        pass
print(f"{errs/total:.4f}" if total else "0.0000")
' || echo 0.0000)
    # Bash float compare via python
    OVER=$(python3 -c "print(1 if float('$ERR_RATE') > $ERROR_RATE_THRESHOLD else 0)")
    if [[ "$OVER" == "1" ]]; then
        emit_trigger "error_rate" "$ERR_RATE" "set_routing_disabled"
        set_routing_disabled
    fi
fi

# --- Graphiti health: PING with 5s timeout ---
if /usr/local/bin/docker exec falkordb-tier2 redis-cli -t 5 PING 2>/dev/null | grep -q PONG; then
    # Healthy → reset failure counter
    echo "0" > "$HEALTH_FILE"
else
    # Unhealthy → increment counter
    PREV=$(cat "$HEALTH_FILE" 2>/dev/null || echo 0)
    NEW=$((PREV + 1))
    echo "$NEW" > "$HEALTH_FILE"
    if (( NEW >= GRAPHITI_FAILURE_RUN_THRESHOLD )); then
        emit_trigger "graphiti_unhealthy" "$NEW" "set_routing_disabled"
        set_routing_disabled
    fi
fi
