#!/usr/bin/env bash
# The red-baseline gate, as an actual runnable command.
#
# WHY THIS SCRIPT EXISTS
# ----------------------
# There is no CI in this repo, so every check is only as real as the command
# someone runs. Two consequences bit us on 2026-08-22:
#
#   1. KOI_REQUIRE_BACKEND was written to make a slow backend FAIL instead of
#      silently skipping — but it had no caller anywhere. It appeared at exactly
#      two lines, both its own definition site in test_cross_type_alias_dedup.py.
#      A default `pytest` run still skipped silently, which is the precise
#      failure it was written to prevent. This script is its caller.
#
#   2. The gate protocol lived in prose, so each run re-derived it slightly
#      differently. The comparison rule below is now fixed in code.
#
# WHAT "SKIP" COSTS HERE, concretely: /health takes 0.2-3.3s and the probe used
# timeout=3. A healthy-but-slow backend read as DOWN, all 5 tests in
# test_cross_type_alias_dedup.py skipped, the 4 nodes it contributes vanished
# from the failure set, and `comm -13` compared empty against empty and printed
# PASS — for a run that never executed them. That produced a bad baseline once
# already, on 2026-08-21.
#
# Usage:  scripts/run-red-baseline-gate.sh [path/to/baseline.txt]

set -uo pipefail

PY="${KOI_PYTHON:-/Users/darrenzal/venvs/koi-server/bin/python}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# NOTE the .notes.txt exclusion: that sidecar also matches *.txt and contains only
# comments, so picking it up yields an EMPTY baseline that every node then looks
# "new" against. Caught by this script failing on its own first run.
BASELINE="${1:-$(ls -1t "$HOME"/koi-backups/red-baseline-B1-*.txt 2>/dev/null | grep -v '\.notes\.txt$' | head -1)}"

# The 8-file node set. STILL A LIST, and that is a known weakness -- three
# defects escaped it on 2026-08-22 by living outside it. The property-based
# companion is tests/test_live_write_governance.py, run below.
NODES=(
  tests/test_type_canonicalization_and_annotation.py
  tests/test_cross_type_alias_dedup.py
  tests/test_fix_entity_types.py
  tests/test_rds_type_normalization.py
  tests/test_fix003_entity_validation.py
  tests/test_fix005_ontology_expansion.py
  tests/test_ontology_loader.py
  tests/unit/test_knowledge_router.py
)

cd "$REPO" || exit 2

if [[ -z "$BASELINE" || ! -f "$BASELINE" ]]; then
  echo "FAIL: no baseline file found (looked for ~/koi-backups/red-baseline-B1-*.txt)" >&2
  exit 2
fi
echo "baseline: $BASELINE"
[[ -f "${BASELINE%.txt}.notes.txt" ]] && echo "notes:    ${BASELINE%.txt}.notes.txt  <- read before interpreting a diff"

# THIS is the line that makes a skip fail instead of passing quietly.
export KOI_REQUIRE_BACKEND=1

CURRENT="$(mktemp)"; trap 'rm -f "$CURRENT"' EXIT
"$PY" -m pytest "${NODES[@]}" -q --tb=no -rf -p no:warnings 2>&1 \
  | grep '^FAILED ' | sed 's/^FAILED //' | awk '{print $1}' | sort -u > "$CURRENT"

BASE_SORTED="$(mktemp)"; trap 'rm -f "$CURRENT" "$BASE_SORTED"' EXIT
grep -v '^#' "$BASELINE" | grep -v '^[[:space:]]*$' | sort -u > "$BASE_SORTED"

NEW="$(comm -13 "$BASE_SORTED" "$CURRENT")"
GONE="$(comm -23 "$BASE_SORTED" "$CURRENT")"

echo "expected failing nodes: $(wc -l < "$BASE_SORTED" | tr -d ' ')"
echo "actual failing nodes:   $(wc -l < "$CURRENT" | tr -d ' ')"

STATUS=0

# An empty result is NOT a pass. If nothing ran, the comparison is vacuous --
# that is exactly how the 2026-08-21 baseline went wrong.
if [[ ! -s "$CURRENT" && -s "$BASE_SORTED" ]]; then
  echo "FAIL: zero failing nodes against a non-empty baseline. Either every known" >&2
  echo "      failure was fixed at once (verify by hand) or the tests did not run." >&2
  STATUS=1
fi

if [[ -n "$NEW" ]]; then
  echo "FAIL: new failing node(s) not in the baseline:" >&2
  echo "$NEW" | sed 's/^/  + /' >&2
  STATUS=1
fi

if [[ -n "$GONE" ]]; then
  echo "NOTE: baseline node(s) no longer failing -- if genuinely fixed, re-cut the baseline:"
  echo "$GONE" | sed 's/^/  - /'
fi

# The property-based half. Unlike the node set above, this finds offenders that
# nobody has listed.
echo
echo "--- live-write governance (property gate) ---"
"$PY" -m pytest tests/test_live_write_governance.py -q --tb=short -p no:warnings || STATUS=1

[[ $STATUS -eq 0 ]] && echo && echo "GATE PASS" || { echo; echo "GATE FAIL"; }
exit $STATUS
