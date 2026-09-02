#!/bin/bash
# Partial indexes backing the /health null-embed drift gauges.
#
# Each predicate normally matches ZERO rows, so the resulting index is ~8KB and
# turns a full seq scan into an index-only scan. Building one still costs a single
# table pass, so run this on a QUIET machine — on 2026-09-01 under load the
# session_chunks scan alone took 198s and CONCURRENTLY builds were killed midway,
# leaving an invalid index behind (drop those with DROP INDEX CONCURRENTLY).
#
# Already built 2026-09-01: knowledge_facts (x2), entity_registry.
# Still needed:  koi_memory_chunks, session_chunks.
# PRE-STEP (2026-09-01): an interrupted CONCURRENTLY build left an INVALID index
# behind. It is inert -- indisvalid=f AND indisready=f, so it is neither read nor
# maintained on writes -- but drop it before rebuilding, or the CREATE below will
# skip it via IF NOT EXISTS and you will keep the dead one:
#     DROP INDEX CONCURRENTLY IF EXISTS idx_kmc_null_embed3072;
# That DROP blocks behind writers on koi_memory_chunks, so run it when quiet.
set -euo pipefail
DB="${1:-personal_koi}"
run() { echo "--- $1"; time psql -d "$DB" -X -q -c "$2"; }
run "koi_memory_chunks" "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_kmc_null_embed3072 ON koi_memory_chunks (id) WHERE embedding_3072 IS NULL;"
run "session_chunks"    "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_sc_null_embed3072  ON session_chunks  (id) WHERE embedding_3072 IS NULL;"
echo "--- validity check (indisvalid must be t for both)"
psql -d "$DB" -X -c "select c.relname, i.indisvalid from pg_class c join pg_index i on i.indexrelid=c.oid where c.relname like 'idx_%null_embed3072%' order by 1;"
