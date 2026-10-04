#!/usr/bin/env bash
# Hermetic regression test for the bounded Substack deep-extraction scheduler.
# Mocks psql and the extractor Python, so it performs no database or API writes.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$TMP/home/.local/bin" "$TMP/venv/bin"
export MOCK_QUERY_CAPTURE="$TMP/query.sql"
export MOCK_PY_CAPTURE="$TMP/python.calls"
export MOCK_PSQL_OUTPUT="$TMP/psql.out"

cat > "$TMP/home/.local/bin/psql" <<'EOF'
#!/bin/bash
query=""
for arg in "$@"; do query="$arg"; done
printf '%s\n' "$query" > "$MOCK_QUERY_CAPTURE"
if [[ "${MOCK_PSQL_FAIL:-0}" = "1" ]]; then
  echo "simulated psql failure"
  exit 7
fi
cat "$MOCK_PSQL_OUTPUT"
EOF
chmod +x "$TMP/home/.local/bin/psql"

cat > "$TMP/venv/bin/python" <<'EOF'
#!/bin/bash
printf '%s\n' "$*" >> "$MOCK_PY_CAPTURE"
echo "  status: ok"
EOF
chmod +x "$TMP/venv/bin/python"

fail() { echo "FAIL: $*" >&2; exit 1; }
assert_contains() {
  local file="$1" expected="$2"
  grep -Fq "$expected" "$file" || fail "$file does not contain: $expected"
}

# The default selects three posts, in the order returned by the newest-first query,
# while reporting the full backlog rather than presenting the selected slice as total.
cat > "$MOCK_PSQL_OUTPUT" <<EOF
substack-corpus:test:newest	5
substack-corpus:test:second	5
substack-corpus:test:third	5
EOF
HOME="$TMP/home" KOI_VENV="$TMP/venv" \
  PSQL_BIN="$TMP/home/.local/bin/psql" \
  SUBSTACK_DEEP_EXTRACT_LOG="$TMP/default.log" \
  /bin/bash "$ROOT/scripts/deep_extract_substack_corpus.sh"

[[ "$(wc -l < "$MOCK_PY_CAPTURE" | tr -d ' ')" = "3" ]] || fail "default limit did not run exactly 3 documents"
sed -n '1p' "$MOCK_PY_CAPTURE" | grep -Fq -- '--document-rid substack-corpus:test:newest' || fail "first RID order changed"
sed -n '2p' "$MOCK_PY_CAPTURE" | grep -Fq -- '--document-rid substack-corpus:test:second' || fail "second RID order changed"
sed -n '3p' "$MOCK_PY_CAPTURE" | grep -Fq -- '--document-rid substack-corpus:test:third' || fail "third RID order changed"
assert_contains "$MOCK_QUERY_CAPTURE" "ORDER BY p.published_at DESC NULLS LAST, p.rid ASC"
assert_contains "$MOCK_QUERY_CAPTURE" "LIMIT 3"
assert_contains "$MOCK_QUERY_CAPTURE" "l.deep_extracted_at IS NOT NULL"
assert_contains "$TMP/default.log" "backlog pending=5 selected=3 limit=3"

# An explicit smaller cap reaches the SQL selector and bounds extractor calls.
: > "$MOCK_PY_CAPTURE"
cat > "$MOCK_PSQL_OUTPUT" <<EOF
substack-corpus:test:newest	5
substack-corpus:test:second	5
EOF
HOME="$TMP/home" KOI_VENV="$TMP/venv" PSQL_BIN="$TMP/home/.local/bin/psql" \
  SUBSTACK_DEEP_EXTRACT_LIMIT=2 \
  SUBSTACK_DEEP_EXTRACT_LOG="$TMP/cap-two.log" \
  /bin/bash "$ROOT/scripts/deep_extract_substack_corpus.sh"
[[ "$(wc -l < "$MOCK_PY_CAPTURE" | tr -d ' ')" = "2" ]] || fail "explicit limit did not run exactly 2 documents"
assert_contains "$MOCK_QUERY_CAPTURE" "LIMIT 2"
assert_contains "$TMP/cap-two.log" "backlog pending=5 selected=2 limit=2"

# Invalid configuration fails before psql or extraction can run.
for invalid_limit in 0 abc -1 1.5; do
  : > "$MOCK_QUERY_CAPTURE"
  : > "$MOCK_PY_CAPTURE"
  if HOME="$TMP/home" KOI_VENV="$TMP/venv" PSQL_BIN="$TMP/home/.local/bin/psql" \
       SUBSTACK_DEEP_EXTRACT_LIMIT="$invalid_limit" \
       SUBSTACK_DEEP_EXTRACT_LOG="$TMP/invalid-$invalid_limit.log" \
       /bin/bash "$ROOT/scripts/deep_extract_substack_corpus.sh"; then
    fail "invalid limit '$invalid_limit' was accepted"
  fi
  [[ ! -s "$MOCK_QUERY_CAPTURE" ]] || fail "psql ran after invalid limit '$invalid_limit'"
  [[ ! -s "$MOCK_PY_CAPTURE" ]] || fail "extractor ran after invalid limit '$invalid_limit'"
  assert_contains "$TMP/invalid-$invalid_limit.log" "must be a positive integer"
done

# A psql error remains distinguishable from an empty backlog and stops extraction.
: > "$MOCK_PY_CAPTURE"
if HOME="$TMP/home" KOI_VENV="$TMP/venv" PSQL_BIN="$TMP/home/.local/bin/psql" \
     MOCK_PSQL_FAIL=1 \
     SUBSTACK_DEEP_EXTRACT_LOG="$TMP/psql-fail.log" \
     /bin/bash "$ROOT/scripts/deep_extract_substack_corpus.sh"; then
  fail "psql failure returned success"
fi
[[ ! -s "$MOCK_PY_CAPTURE" ]] || fail "extractor ran after psql failure"
assert_contains "$TMP/psql-fail.log" "ERROR: psql query failed (exit 7): simulated psql failure"

echo "PASS: deep_extract_substack_corpus scheduling is bounded and fail-closed"
