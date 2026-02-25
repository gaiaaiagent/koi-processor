#!/usr/bin/env bash
set -euo pipefail

PHASE=""
FRESH=""
for arg in "$@"; do
    case "$arg" in
        --fresh) FRESH="--fresh" ;;
        0a|0b|all|clean) PHASE="$arg" ;;
        *) echo "Unknown argument: $arg"; echo "Usage: $0 [0a|0b|all|clean] [--fresh]"; exit 1 ;;
    esac
done
PHASE="${PHASE:-0a}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ARTIFACTS_DIR="$SCRIPT_DIR/artifacts/$(date -u +%Y%m%dT%H%M%SZ)"

if [[ "$FRESH" == "--fresh" ]]; then
    echo "=== Fresh mode: removing existing containers and volumes ==="
    docker rm -f terminusdb terminusdb-shawn 2>/dev/null || true
    docker volume rm terminusdb_data terminusdb_shawn_data 2>/dev/null || true
fi

mkdir -p "$ARTIFACTS_DIR"

echo "=== Phase 0 TerminusDB Evaluation ==="
echo "Phase: $PHASE | Fresh: ${FRESH:-no} | Artifacts: $ARTIFACTS_DIR"
echo "Started: $(date -u +%Y-%m-%dT%H:%M:%SZ)"

# Activate venv if it exists
if [[ -f "$PROJECT_ROOT/venv/bin/activate" ]]; then
    source "$PROJECT_ROOT/venv/bin/activate"
fi

if [[ "$PHASE" == "0a" || "$PHASE" == "all" ]]; then
    echo -e "\n--- Step 1: Start TerminusDB ---"
    if docker ps --format '{{.Names}}' | grep -q '^terminusdb$'; then
        echo "terminusdb already running"
    elif docker ps -a --format '{{.Names}}' | grep -q '^terminusdb$'; then
        echo "terminusdb exists but stopped, starting..."
        docker start terminusdb
        sleep 3
    else
        docker run -d --name terminusdb \
            -p 6363:6363 \
            -v terminusdb_data:/app/terminusdb/storage \
            terminusdb/terminusdb-server:v12
        echo "Waiting for TerminusDB to start..."
        sleep 5
    fi

    # Health check with retry
    for i in {1..10}; do
        if curl -sf http://localhost:6363/api/info > /dev/null 2>&1; then
            echo "TerminusDB is ready"
            curl -sf http://localhost:6363/api/info | python3 -m json.tool
            break
        fi
        echo "Waiting for TerminusDB... ($i/10)"
        sleep 2
    done

    echo -e "\n--- Step 2: Import ---"
    cd "$PROJECT_ROOT"
    python3 -m scripts.terminusdb.import_from_postgres 2>&1 | tee "$ARTIFACTS_DIR/import.log"

    echo -e "\n--- Step 3: Merge Tests ---"
    python3 -m scripts.terminusdb.test_merge 2>&1 | tee "$ARTIFACTS_DIR/test_merge.log"

    echo -e "\n--- Step 4: Metrics ---"
    docker stats --no-stream terminusdb > "$ARTIFACTS_DIR/docker_stats.txt" 2>&1 || true
    cp "$SCRIPT_DIR/results.json" "$ARTIFACTS_DIR/" 2>/dev/null || true
fi

if [[ "$PHASE" == "0b" || "$PHASE" == "all" ]]; then
    # Precondition: 0a must have passed
    if [[ ! -f "$SCRIPT_DIR/results.json" ]]; then
        echo "ERROR: Phase 0a results not found. Run 0a first."
        exit 1
    fi
    GO=$(python3 -c "import json; r=json.load(open('$SCRIPT_DIR/results.json')); print(r[-1]['go_nogo'] if r[-1]['phase']=='0a' else 'missing_0a')")
    if [[ "$GO" != "go" ]]; then
        echo "ERROR: Phase 0a did not pass (go_nogo=$GO). Cannot run 0b."
        exit 1
    fi

    echo -e "\n--- Step 5: Second Instance ---"
    if docker ps --format '{{.Names}}' | grep -q '^terminusdb-shawn$'; then
        echo "terminusdb-shawn already running"
    elif docker ps -a --format '{{.Names}}' | grep -q '^terminusdb-shawn$'; then
        echo "terminusdb-shawn exists but stopped, starting..."
        docker start terminusdb-shawn
        sleep 3
    else
        docker run -d --name terminusdb-shawn \
            -p 6364:6363 \
            -v terminusdb_shawn_data:/app/terminusdb/storage \
            terminusdb/terminusdb-server:v12
        echo "Waiting for terminusdb-shawn to start..."
        sleep 5
    fi

    # Health check
    for i in {1..10}; do
        if curl -sf http://localhost:6364/api/info > /dev/null 2>&1; then
            echo "terminusdb-shawn is ready"
            break
        fi
        echo "Waiting for terminusdb-shawn... ($i/10)"
        sleep 2
    done

    echo -e "\n--- Step 6: Federation Tests ---"
    cd "$PROJECT_ROOT"
    python3 -m scripts.terminusdb.test_federation 2>&1 | tee "$ARTIFACTS_DIR/test_federation.log"

    echo -e "\n--- Step 7: Metrics ---"
    docker stats --no-stream terminusdb terminusdb-shawn > "$ARTIFACTS_DIR/docker_stats_both.txt" 2>&1 || true
    cp "$SCRIPT_DIR/results.json" "$ARTIFACTS_DIR/" 2>/dev/null || true
fi

if [[ "$PHASE" == "clean" ]]; then
    docker rm -f terminusdb terminusdb-shawn 2>/dev/null || true
    docker volume rm terminusdb_data terminusdb_shawn_data 2>/dev/null || true
    echo "Cleaned up."
fi

echo -e "\n=== Done. Artifacts: $ARTIFACTS_DIR ==="
