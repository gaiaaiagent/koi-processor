# Phase 1 — read-path swap map (applyable)

Audience model (operator-confirmed 2026-07-14): **koi-processor `:8351` is the TEAM backend** (the public Living Library is a separate app). So:
- **team-facing *surfacing* reads → `team` audience** (hide confidential + unclassified after backfill)
- **internal machinery (resolvers, matchers, `COUNT(*)` stats) → LEAVE raw `NOT node_private`** (they must see everything; team-filtering a resolver causes duplicate-entity corruption)
- **stats/counts → true totals** (leave raw) — operator chose true totals over team-masked

Predicate: `visibility_predicate('entity_registry', scopes_for('team'))` from `api/policy/visibility.py`, which emits `(NOT COALESCE(entity_registry.node_private,false) AND entity_registry.visibility_scope IN ('public','team'))` — a true drop-in for `AND NOT node_private` plus the audience gate.

**Import to add** (top of `api/personal_ingest_api.py` and `api/retrieval_executors.py`):
```python
from api.policy.visibility import visibility_predicate, scopes_for
```

## SWAP → team (10 sites)

Each replaces `AND NOT node_private` with `AND {visibility_predicate('entity_registry', scopes_for('team'))}`. Note the string style per site (some are plain strings that must become f-strings).

| # | File:line | Function | String style | Edit |
|---|---|---|---|---|
| 1 | personal_ingest_api.py:2204 | `_semantic_entity_search` (embedding) | f-string block | `WHERE embedding_3072 IS NOT NULL AND {visibility_predicate('entity_registry', scopes_for('team'))}` |
| 2 | personal_ingest_api.py:2707 | `list_entities` | f-string block | `WHERE entity_type = $1 AND {…team…}` |
| 3 | personal_ingest_api.py:2715 | `list_entities` | f-string block | `WHERE {…team…}` (drop the bare `NOT node_private`) |
| 4 | personal_ingest_api.py:2760 | `entity_search` | f-string block | `WHERE normalized_text ILIKE $2 AND entity_type = $3 AND {…team…}` |
| 5 | personal_ingest_api.py:2773 | `entity_search` | f-string block | `WHERE normalized_text ILIKE $2 AND {…team…}` |
| 6 | personal_ingest_api.py:3296 | `get_entity_evidence` | **plain string → make f-string** | `f"SELECT entity_text FROM entity_registry WHERE fuseki_uri = $1 AND {visibility_predicate('entity_registry', scopes_for('team'))}"` |
| 7 | personal_ingest_api.py:3402 | `get_entity` | f-string block | `WHERE fuseki_uri = $1 AND {…team…}` |
| 8 | personal_ingest_api.py:5455 | `_graph_guided_retrieval` (embedding) | f-string block | `WHERE embedding_3072 IS NOT NULL AND {…team…}` |
| 9 | personal_ingest_api.py:5477 | `_graph_guided_retrieval` (conditions) | f-string block | `WHERE ({conditions}) AND {…team…}` |
| 10 | retrieval_executors.py:51 | `entity_lookup` (the PolicyScope seam) | **needs param** | see below |

**Site 10 (the seam)** — not a text swap. `entity_lookup(...)` already has `include_node_private: bool = False`. Add an `audience: str = 'team'` param and change:
```python
privacy_filter = "" if include_node_private else "AND NOT node_private"
```
to:
```python
privacy_filter = "" if include_node_private else f"AND {visibility_predicate('entity_registry', scopes_for(audience))}"
```
The chat/RAG caller (chat_endpoint) passes `audience='team'` (or threads the request's audience when per-user audiences exist).

## LEAVE RAW `NOT node_private` (~12 sites — do NOT swap)

Internal machinery that must see everything:
- `_resolve_entity_uri` (personal_ingest_api.py:5732, 5739) — name→uri resolver
- `_resolve_extra_label_candidates` (web_router.py:449, 473) — label resolver
- `web_evaluate` entity-matching context (web_router.py:663, 757) — extractor dedup context
- `get_stats` counts (personal_ingest_api.py:3484, 3489, 3497) — true-total metrics
- `graph_version` entity_count (personal_ingest_api.py:2355) — true-total metric
- `COUNT(*)` (personal_ingest_api.py:5850) — internal count
- `knowledge_health.py:316`, `b8a_enrich_entities.py:116` — batch script metrics/enrichment

## Deploy sequence (ONE coordinated, gated operation — NOT split across time)

1. **`pg_dump` verified** (have: `personal_koi_clean_20260714T180257.dump`).
2. **Backfill `--apply`** — classifies 17,422 → team, 5 → confidential, 4,144 stay unclassified. *(Gate: the confidential 5 are the obvious denylist; the 4 Eve-candidates stay held; safe over-restrictive default.)* **Must run BEFORE the swap** — otherwise the team predicate returns nothing (all rows currently unclassified → team-audience = empty graph).
3. **Apply the 10 swap edits + the import** (this branch).
4. **Test read-only via psql**: each swapped query now returns the 17,422 team rows (not empty); a confidential entity 404s for team; a resolver still finds any entity.
5. **Restart the koi-processor launchd backend** (`~/.config/personal-koi/restart.sh`) — the risky moment; rollback = `git checkout` the swap + restart, or the DOWN migration + dump.
6. **Verify live**: MCP/team reads return team entities; `visible_at('hydro-one','team')`→False; backend healthy.

## ⚠ Deploy-time decision: the 4,144 hidden-from-team entities

After the swap, the **4,144 conservatively-`unclassified`** entities (email-sourced third-party correspondence + ambiguous) **disappear from team search/listing** until triaged. For a 5-person trusted team that may be over-restrictive. Options:
- **(a)** Accept it — they're triage-pending; the conservative default is deliberate (third-party consent). Triage `Meta/Indigenomics-Classify-Triage.md` over time.
- **(b)** Broaden the backfill Rule 5 to also team-stamp the non-email internal-ambiguous subset (keep only email-sensor/proton-email + explicit third-party as unclassified), shrinking the hidden set.
Decide before step 2.
