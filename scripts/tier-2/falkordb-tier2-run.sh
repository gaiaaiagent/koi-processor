#!/bin/zsh
# FalkorDB tier-2 LaunchAgent wrapper.
#
# Per Tier-2 plan §Operational concerns "Production runtime layout":
#   - LaunchAgent: com.falkordb.tier2
#   - Volume:      ~/.falkordb-tier2-data/  → /var/lib/falkordb/data inside container
#   - Port:        6380 (avoid 6379 system Redis collision)
#   - Image:       falkordb/falkordb:v4.18.2 (POC-validated floor)
#
# Strategy: foreground `docker run` so LaunchAgent can supervise + restart.
# The container is removed (--rm) on stop; data persists via the bind-mount.
# Pre-removes any stale container with the same name (e.g., from prior LaunchAgent
# run that didn't fully clean up) before starting.

set -e

CONTAINER_NAME="falkordb-tier2"
IMAGE="falkordb/falkordb:v4.18.2"
HOST_PORT="6380"
DATA_DIR="$HOME/.falkordb-tier2-data"

# Ensure data dir exists
mkdir -p "$DATA_DIR"

# Pre-remove any stale container with the same name. LaunchAgent restart cycles
# may leave a container if `docker run --rm` didn't run its cleanup hook.
docker rm -f "$CONTAINER_NAME" 2>/dev/null || true

# Apply runtime config (TIMEOUT_DEFAULT/TIMEOUT_MAX) once the container is up.
# Backgrounded subshell polls PING; when responsive, sets GRAPH.CONFIG. Idempotent.
(
    for i in 1 2 3 4 5 6 7 8 9 10; do
        if /usr/local/bin/docker exec "$CONTAINER_NAME" redis-cli PING 2>/dev/null | grep -q PONG; then
            /usr/local/bin/docker exec "$CONTAINER_NAME" redis-cli GRAPH.CONFIG SET TIMEOUT_DEFAULT 30000 >/dev/null 2>&1 || true
            /usr/local/bin/docker exec "$CONTAINER_NAME" redis-cli GRAPH.CONFIG SET TIMEOUT_MAX 60000 >/dev/null 2>&1 || true
            break
        fi
        sleep 1
    done
) &

# Run container in foreground (LaunchAgent supervises this process).
# - --rm: remove on exit (data persists in bind-mount).
# - -p 6380:6379: expose FalkorDB on host port 6380.
# - -v: bind-mount production data dir.
exec /usr/local/bin/docker run --rm \
    --name "$CONTAINER_NAME" \
    -p "$HOST_PORT":6379 \
    -v "$DATA_DIR":/var/lib/falkordb/data \
    "$IMAGE"
