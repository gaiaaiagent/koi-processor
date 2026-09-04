#!/bin/bash
# Correct way to restart the launchd-managed koi-processor service.
#
# WHY THIS EXISTS:
#   The koi backend runs as a launchd job (com.personal.koi-processor, foreground
#   mode, KeepAlive). The launchd instance writes no PID file, so:
#     - stop.sh CANNOT stop it (stop.sh only handles background-mode PID-file instances)
#     - start.sh in background mode spawns a SECOND, launchd-untracked uvicorn that
#       races on port 8351
#   Without this script, a session needing to "restart the backend" reaches for raw
#   `launchctl kickstart -k` / `unload`+`load` in an uncoordinated way. This script
#   is the single correct, idempotent restart primitive — use it instead.
#
# WHY THE WAIT LOOP LOOKS LIKE THIS (2026-09-03):
#   It used to be `for i in $(seq 1 30); do curl ...; sleep 1; done`. When the port is
#   refused curl fails in microseconds, so that budget collapsed to ~30s of wall clock.
#   Startup takes ~40-50s whenever Postgres is busy — measured twice tonight during a
#   pg_dump, healthy at t+40s and t+50s. The script printed ERROR both times on restarts
#   that had SUCCEEDED.
#
#   That is worse than having no check. It is the inverse of the failure this project has
#   already recorded ("verification code must fail loud, not open"): this one failed loud
#   when everything was fine, which teaches the operator to ignore it — and the next time
#   it means something, they won't believe it.
#
#   So: poll to a wall-clock DEADLINE, and distinguish "not up yet" from "did not start".
#   A timeout now reports which of those it was, because they need different responses.
set -uo pipefail

LABEL="com.personal.koi-processor"
TARGET="gui/$(id -u)/$LABEL"
HEALTH="http://localhost:8351/health"
STDERR_LOG="$HOME/.config/personal-koi/stderr.log"

# Generous by default: the cost of waiting is seconds, the cost of a false ERROR is that
# the check stops being believed. Early exits below catch a genuinely dead process long
# before this elapses.
TIMEOUT="${KOI_RESTART_TIMEOUT:-180}"

job_pid() { launchctl list 2>/dev/null | awk -v l="$LABEL" '$3 == l { print $1 }'; }

if ! launchctl print "$TARGET" > /dev/null 2>&1; then
    echo "ERROR: launchd job $LABEL is not loaded." >&2
    echo "  Cold-start it with:" >&2
    echo "    launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/$LABEL.plist" >&2
    exit 1
fi

echo "[koi-restart] kickstarting $LABEL ..."
# kickstart -k: stop the running instance (SIGTERM, then SIGKILL on timeout) and
# start it fresh. launchctl serializes this, so concurrent callers cause at worst
# a brief flap — never a rogue instance or a port-conflict retry loop.
launchctl kickstart -k "$TARGET"

START=$(date +%s)
DEADLINE=$((START + TIMEOUT))
missing_streak=0
seen_pids=""
n_pids=0
last_note=0

while [ "$(date +%s)" -lt "$DEADLINE" ]; do
    if curl -sf --max-time 4 "$HEALTH" > /dev/null 2>&1; then
        echo "[koi-restart] healthy after $(( $(date +%s) - START ))s"
        exit 0
    fi

    pid="$(job_pid)"
    if [ -z "$pid" ] || [ "$pid" = "-" ]; then
        # A brief gap right after kickstart is normal; a sustained one is not.
        missing_streak=$((missing_streak + 1))
        if [ "$missing_streak" -ge 15 ]; then
            echo "ERROR: $LABEL has no running process ${missing_streak}s after kickstart." >&2
            echo "       This is a FAILURE TO START, not a slow start." >&2
            echo "       Last stderr line:" >&2
            tail -n 1 "$STDERR_LOG" 2>/dev/null | sed 's/^/         /' >&2
            exit 1
        fi
    else
        missing_streak=0
        case " $seen_pids " in
            *" $pid "*) ;;
            *) seen_pids="$seen_pids $pid"; n_pids=$((n_pids + 1)) ;;
        esac
        # KeepAlive respawns a crashing process, so a moving PID is the signature of a
        # crash loop rather than of a slow boot. Waiting out the full timeout on one of
        # those just delays the same answer.
        if [ "$n_pids" -ge 4 ]; then
            echo "ERROR: $LABEL is CRASH-LOOPING — ${n_pids} distinct PIDs in $(( $(date +%s) - START ))s:$seen_pids" >&2
            echo "       launchd KeepAlive is respawning it. Check:" >&2
            tail -n 5 "$STDERR_LOG" 2>/dev/null | sed 's/^/         /' >&2
            exit 1
        fi
    fi

    # Say something periodically so a legitimately slow start does not read as a hang.
    now=$(date +%s)
    if [ $((now - last_note)) -ge 15 ]; then
        echo "[koi-restart] still starting ($(( now - START ))s elapsed, pid=${pid:-none}) ..."
        last_note=$now
    fi
    sleep 1
done

elapsed=$(( $(date +%s) - START ))
pid="$(job_pid)"
if [ -n "$pid" ] && [ "$pid" != "-" ]; then
    echo "ERROR: $LABEL is RUNNING (pid $pid) but /health did not answer within ${elapsed}s." >&2
    echo "       The process is alive, so this is a startup that is hung or very slow —" >&2
    echo "       not a failure to launch. Raise the budget with KOI_RESTART_TIMEOUT=<seconds>" >&2
    echo "       if the database is under load (a pg_dump makes startup take ~40-50s)." >&2
else
    echo "ERROR: $LABEL is NOT RUNNING after ${elapsed}s." >&2
fi
echo "       Last stderr line:" >&2
tail -n 1 "$STDERR_LOG" 2>/dev/null | sed 's/^/         /' >&2
exit 1
