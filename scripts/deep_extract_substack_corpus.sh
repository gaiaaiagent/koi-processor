#!/bin/bash
# Daily deep-extraction of newly-ingested substack-corpus posts into the discourse graph.
#
# The substack sensor (substack_sensor.py, scheduled separately ~07:15) only INGESTS +
# embeds new posts — it does NOT deep-extract them. This job runs after it (~07:45), finds
# up to a small per-run limit of substack-corpus:* posts that have no completed
# deep-extraction yet, and runs the thorough-tier extractor (entities + facts +
# discourse moves) on each. Newest posts run first; older and undated posts remain
# pending for later runs.
#
# Generalized to ALL substack-corpus feeds (not just indyjohar) via the source_sensor
# filter. Idempotent + resumable: extract_deep_documents.py caches windows and takes a
# per-doc advisory lock, so overlap with a manual run or a re-fire is safe. Sequential —
# daily volume is ordinarily 0-2 posts. The default limit of 3 absorbs a small daily
# burst while preventing a newly-added historical corpus from becoming one unbounded job.
#
# Extraction transport defaults to the Claude Code SUBSCRIPTION via `claude -p`
# (DOC_EXTRACTOR_TRANSPORT=claude_p, $0 marginal cost) — set DOC_EXTRACTOR_TRANSPORT=api
# to use the pay-per-token ANTHROPIC_API_KEY instead (faster, but billed). Either way,
# posts that fail extract individually, are logged, and the next run retries them
# (resumable); the job exits cleanly (no crash-loop).
set -uo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SELF_DIR/.." && pwd)"
cd "$REPO_ROOT" || exit 9

# launchd runs this under macOS system /bin/bash (3.2) with a minimal PATH that
# lacks Homebrew bins — psql lives in /opt/homebrew/bin. Prepend it (and ~/.local/bin,
# where the `claude` CLI lives — needed for the subscription extraction transport) so
# both resolve the same way they do in an interactive shell.
export PATH="/opt/homebrew/bin:$HOME/.local/bin:$PATH"

VENV="${KOI_VENV:-/Users/darrenzal/venvs/koi-server}"
PY="$VENV/bin/python"
LOG_DIR="$REPO_ROOT/logs"
mkdir -p "$LOG_DIR"
SOURCE_SENSOR="substack-corpus-backfill"   # what substack_sensor.py stamps on every row

ts() { date '+%F %T'; }

set -a; source config/personal.env 2>/dev/null; set +a
LOG="${SUBSTACK_DEEP_EXTRACT_LOG:-$LOG_DIR/substack-deep-extract.log}"
# Positive integer; configure in the launchd environment or personal.env for a
# deliberately larger catch-up run. Three is the bounded daily default described above.
RUN_LIMIT="${SUBSTACK_DEEP_EXTRACT_LIMIT:-3}"
export DOC_INGEST_KOI_URL="${DOC_INGEST_KOI_URL:-http://localhost:8351}"
PSQL_URL="${POSTGRES_URL:-postgresql://darrenzal:@localhost:5432/personal_koi}"
PSQL_BIN="${PSQL_BIN:-psql}"

if [[ ! -x "$PY" ]]; then echo "[$(ts)] ERROR: venv python not found at $PY" >> "$LOG"; exit 1; fi
if [[ ! "$RUN_LIMIT" =~ ^[1-9][0-9]*$ ]]; then
  echo "[$(ts)] ERROR: SUBSTACK_DEEP_EXTRACT_LIMIT must be a positive integer; got '$RUN_LIMIT'" >> "$LOG"
  exit 2
fi

# Un-extracted substack-corpus posts = ingested rows with no completed deep-extraction.
# NOTE: `mapfile` is a bash-4 builtin; macOS system /bin/bash (used by launchd) is
# 3.2 and lacks it — read into the array with a portable while-loop instead.
#
# The query's output goes to a temp file rather than a `< <(psql ...)` process
# substitution: bash cannot see a process substitution's exit status through the
# while-loop's `$?`, so a failed psql (bad DSN, network blip, syntax error after an
# edit) produced an empty RIDS array indistinguishable from the genuine "0 rows
# pending" case below — "nothing to extract" and "the query never ran" logged
# identically. Capturing psql's own exit code makes that distinction explicit.
RIDS=()
TOTAL_PENDING=0
PSQL_OUT="$(mktemp)"
trap 'rm -f "$PSQL_OUT"' EXIT
"$PSQL_BIN" "$PSQL_URL" -tA -F $'\t' -c "
  WITH pending AS (
    SELECT m.rid, m.published_at
    FROM koi_memories m
    WHERE m.source_sensor = '$SOURCE_SENSOR'
      AND m.rid LIKE 'substack-corpus:%'
      AND NOT EXISTS (
        SELECT 1 FROM document_ingestion_log l
        WHERE l.document_rid = m.rid AND l.deep_extracted_at IS NOT NULL)
  )
  SELECT p.rid, COUNT(*) OVER () AS total_pending
  FROM pending p
  ORDER BY p.published_at DESC NULLS LAST, p.rid ASC
  LIMIT $RUN_LIMIT;" > "$PSQL_OUT" 2>&1
psql_status=$?
if [[ "$psql_status" -ne 0 ]]; then
  echo "[$(ts)] ERROR: psql query failed (exit $psql_status): $(cat "$PSQL_OUT")" >> "$LOG"
  exit 1
fi
while IFS=$'\t' read -r _rid _total_pending; do
  [[ -z "$_rid" ]] && continue
  if [[ ! "$_total_pending" =~ ^[0-9]+$ ]]; then
    echo "[$(ts)] ERROR: malformed psql output for '$_rid': expected pending count, got '$_total_pending'" >> "$LOG"
    exit 1
  fi
  if [[ "$TOTAL_PENDING" -ne 0 && "$TOTAL_PENDING" -ne "$_total_pending" ]]; then
    echo "[$(ts)] ERROR: inconsistent pending counts in psql output: $TOTAL_PENDING and $_total_pending" >> "$LOG"
    exit 1
  fi
  TOTAL_PENDING="$_total_pending"
  RIDS+=("$_rid")
done < "$PSQL_OUT"

N=${#RIDS[@]}
echo "[$(ts)] substack deep-extraction backlog pending=$TOTAL_PENDING selected=$N limit=$RUN_LIMIT" >> "$LOG"
if [[ "$N" -eq 0 ]]; then
  echo "[$(ts)] nothing to extract — all substack-corpus posts already in the discourse graph" >> "$LOG"
  exit 0
fi
echo "[$(ts)] deep-extracting $N selected substack-corpus post(s), newest published first" >> "$LOG"

ok=0; fail=0
for rid in "${RIDS[@]}"; do
  out=$("$PY" scripts/extract_deep_documents.py \
          --document-rid "$rid" --tier thorough --source-sensor "$SOURCE_SENSOR" 2>&1)
  st=$(printf '%s\n' "$out" | grep -m1 -E "^  status:|ExtractionError|Traceback" | sed 's/^ *//')
  printf '[%s] %-62s | %s\n' "$(ts)" "$rid" "${st:-NO_STATUS}" >> "$LOG"
  if printf '%s' "$st" | grep -q "status: ok"; then ok=$((ok+1)); else fail=$((fail+1)); fi
done
echo "[$(ts)] DONE ok=$ok fail=$fail" >> "$LOG"
