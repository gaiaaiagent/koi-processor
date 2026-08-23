#!/usr/bin/env bash
# Shared opt-in and fail-closed cleanup contract for live-writing shell suites.

LIVE_WRITE_GUARD_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
LIVE_WRITE_PYTHON=${KOI_PYTHON:-/Users/darrenzal/venvs/koi-server/bin/python}

live_write_begin() {
    if [ "${KOI_ALLOW_LIVE_TEST_WRITES:-0}" != "1" ]; then
        echo "Refusing persistent test writes: set KOI_ALLOW_LIVE_TEST_WRITES=1" >&2
        return 2
    fi
    if [ -z "${KOI_LIVE_POSTGRES_URL:-}" ]; then
        echo "Refusing persistent test writes: KOI_LIVE_POSTGRES_URL is required" >&2
        return 2
    fi
    "$LIVE_WRITE_PYTHON" "$LIVE_WRITE_GUARD_DIR/live_write_cleanup.py" \
        validate --dsn "$KOI_LIVE_POSTGRES_URL" >/dev/null
    KOI_TEST_RUN_ID=${KOI_TEST_RUN_ID:-$(uuidgen | tr '[:upper:]' '[:lower:]')}
    if ! [[ "$KOI_TEST_RUN_ID" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]]; then
        echo "Refusing persistent test writes: KOI_TEST_RUN_ID must be a UUID" >&2
        return 2
    fi
    KOI_CLEANUP_MANIFEST=$(mktemp)
    export KOI_TEST_RUN_ID KOI_CLEANUP_MANIFEST
    trap live_write_finish EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
}

live_write_record() {
    local kind=$1 value=${2:-}
    [ -z "$value" ] && return 0
    "$LIVE_WRITE_PYTHON" "$LIVE_WRITE_GUARD_DIR/live_write_cleanup.py" record \
        --manifest "$KOI_CLEANUP_MANIFEST" --kind "$kind" --value "$value"
}

live_write_finish() {
    local status=$?
    trap - EXIT INT TERM
    if ! "$LIVE_WRITE_PYTHON" "$LIVE_WRITE_GUARD_DIR/live_write_cleanup.py" cleanup \
        --dsn "$KOI_LIVE_POSTGRES_URL" \
        --manifest "$KOI_CLEANUP_MANIFEST" \
        --run-id "$KOI_TEST_RUN_ID"; then
        status=1
    fi
    rm -f "$KOI_CLEANUP_MANIFEST"
    exit "$status"
}
