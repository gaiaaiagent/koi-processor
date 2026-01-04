# Regen Network Knowledge Graph Quality Review - Cycle 2026-02

**Started:** 2026-01-03
**Last Updated:** 2026-01-03
**Status:** Planning — define intent-aware retrieval + source hygiene workstream
**Graph URL:** https://regen.gaiaai.xyz/graph
**Server:** ssh darren@202.61.196.119
**Primary Repo:** koi-processor

**Previous Cycle:** [knowledge-graph-review-2026-01.md](knowledge-graph-review-2026-01.md)

---

## Day-1 Baseline (TBD)

*Captured via `scripts/kg_audit_report.py`*

### Summary Metrics

| Metric | Value |
|--------|-------|
| Entities (entity_registry) | TBD |
| Relationships (koi_relationships) | TBD |
| Distinct Predicates | TBD |
| Quality Gates | TBD |

---

## Carry-Over from 2026-01

1. Ambiguous single-token PERSONs with relationships (manual review optional).
2. Deferred alias: `registry` → Regen Registry (ambiguous).
3. Optional predicate consolidation (long-tail cleanup) if desired.

---

## Planned Work

### Workstream A — Intent-Aware Retrieval + Source Hygiene (High Priority)

**Problem statement (observed failure mode):**
The retrieval layer can return “high match” documents that **mention** an entity (e.g., alias tables, planning docs, code files) but do not provide **evidence** for the user’s intent (e.g., “What is PERSON working on?”). This leads to misleading grounding and irrelevant citations in end-user answers.

Example pattern:
- Question: “What is Greg Landua working on?”
- Bad-but-relevant source: an internal KG planning/implementation doc that contains “Greg Landua → Gregory Landua” as an alias example.
- Root cause: retrieval optimized for mention/entity match, not intent-aligned evidence.

**Goal:**
For public-facing Q&A, prefer citations that actually support the claim (activity-bearing evidence) and avoid internal/infrastructure artifacts by default.

#### A1) Add “retrieval profile” selection to `POST /api/koi/query`
**Key design choice:** treat `intent` + (optional) `source_policy` as a server-side **retrieval profile selector** (filters + weights + time window + min-evidence threshold), not as a freeform client toggle.

Add optional fields to the request body (additive; maintain backwards compatibility):
- `intent`: `"general"` (default) | `person_activity` | `person_bio` | `concept_explain` | `technical_howto` | `code_navigation`
- `source_policy`: `"public"` | `"internal_ok"` (requested policy)
- `published_from` / `published_to` (optional; allow caller override when appropriate)

**Security / enforcement rule (important):**
`source_policy` must not be a client-controlled “data exfil” switch. Compute an **effective policy** server-side:
- `allowed_policy` depends on the caller context (public vs internal)
- `effective_policy = min(requested_policy, allowed_policy)`

**Policy ordering + fail-closed defaults (required):**
- Ordering: `public < internal_ok`
- For public surfaces, set `allowed_policy=public` (ignore `internal_ok` requests).
- Treat `visibility=unknown` as **internal** when `effective_policy=public` (missing metadata must not leak).

Practical enforcement options (pick one to implement first):
1. **Public endpoint enforcement:** for the public Custom GPT surface, treat `allowed_policy="public"` (ignore/override `internal_ok` requests).
2. **Internal key escalation (if needed):** allow `internal_ok` only when a trusted internal header is present (e.g., `X-Internal-API-Key`) and nginx routes the request internally.

#### A1.1) Prefer metadata-based filtering over URL-prefix rules
URL-prefix filters (`exclude_url_prefixes`) are brittle (mirrors/proxies/renames). Prefer filtering/weighting on document-level metadata. KOI already has some of this (examples seen in the codebase/corpus):
- `is_private` (or equivalent)
- `source_sensor` / `source_type` / `source`
- `rid` structure (often encodes origin)

Define a minimal taxonomy (computed at ingestion or derived at query-time):
- `visibility`: `public | internal`
- `source_kind`: `forum | web | github | notion | social | ops`
- `doc_kind`: `post | article | markdown | code | plan | dump | issue | pr | release_note`
- `repo`: repo name (for github sources)

Fallback-only (if metadata missing): allow URL-prefix exclusions as a stopgap, but keep the long-term plan metadata-driven.

#### A2) Add request-time “intent” to drive reranking + evidence gating
Add an optional `intent` enum (minimal set; expand over time):
- `person_activity` (e.g., “what is X working on?”)
- `person_bio` (e.g., “who is X?”)
- `concept_explain` (e.g., “what is X?”)
- `technical_howto` (e.g., “how does X work?”)
- `code_navigation` (explicit code/repo questions)

For `person_activity`, apply **evidence gating** and downrank “mere mention” artifacts:
- Apply a recency window by default (e.g., prefer last 6–12 months) so old work isn’t phrased as “current”.
  - Recency should be based on `published_at` (content timestamp), not DB ingestion time (`created_at`), to avoid bulk re-ingestion making old docs appear “new”.
  - Suggested behavior (MVP, testable):
    - Filter to the last 12 months using `published_at`.
    - If fewer than 3 candidates remain, expand to 24 months and return a warning + `recency_window_used=24mo`.
    - If `published_at` is missing, treat the document as out-of-window for `person_activity`; if all candidates are undated, return `answerable=false` with reason `no_dated_sources`.
- Prefer sources with activity-bearing evidence (e.g., “working on”, “leading”, “proposal”, “roadmap”, “announced”, “released”).
- Also treat **authored-by-person** forum posts as activity evidence (within the recency window), even if the person’s full name is not present in the body text (retrieve via `author` metadata / sensor-provided username).
- Downrank/omit documents that only support identity disambiguation (alias tables, index manifests, test fixtures) unless the user asked for identity resolution.
- Prefer public, human-facing URLs (forum threads/posts, registry pages, blogs) over internal/infrastructure repos.

**Important nuance for GitHub sources:**
- Avoid blanket “exclude github” rules.
- For `person_activity` + `effective_policy=public`, treat `doc_kind=code|plan|dump` as low-quality evidence by default.
- Allow (or boost) `doc_kind=issue|pr|release_note` if we index those as first-class docs, because they can be strong public activity evidence.

Implementation options (choose one as we implement; start with simplest):
1. **Hard filter**: remove disallowed sources first, then rank.
2. **Soft rerank**: keep but penalize (better recall, more complexity).
3. **Two-stage**: retrieve broad → classify evidence_type → filter/answer.

#### A2.1) “Answerability” signal (recommended)
Return a small, explicit signal in the response so callers (including GPT) can avoid overconfident narrative when evidence is weak:
- `answerable`: boolean
- `answerability_reason`: short string enum (e.g., `no_recent_sources`, `sources_only_identity_mentions`, `policy_filtered_all_sources`)
- `evidence_summary`: optional short string (e.g., “2 forum posts within 6 months mention active work on X”)

Add: `ambiguous_entity` reason for polysemy (multiple PERSON matches). For `person_activity`, prefer returning `answerable=false` + “please clarify which PERSON” over guessing.

#### A2.2) Profile Observability (recommended)
To keep regressions diagnosable, return a small “profile debug” object (public-safe) in the response:
- `profile_name` + `profile_version` (e.g., `person_activity_public_v1`)
- `effective_policy` (computed server-side)
- `recency_window_used` (e.g., `12mo`, `24mo`, `none`)
- counts: `candidates_total`, `candidates_filtered`, `candidates_kept`

This keeps responsibilities clean:
- Server controls what evidence is available and whether it meets a threshold.
- GPT/clients are instructed to say “not confirmed” when `answerable=false`.

#### A3) Evidence-type tagging (optional but powerful)
Add an internal (not necessarily exposed) classifier to tag each candidate result:
- `evidence_type`: `activity` | `identity` | `definition` | `implementation` | `ops`

For `person_activity`, only allow `activity` citations to support claims. Allow `identity` only for disambiguation (not as evidence of activity).

#### A4) Supporting snippets / spans (recommended)
Doc-level citations are not enough for enforcing “mention ≠ evidence”. Prefer returning the exact supporting excerpt(s) used:
- Ensure `citations[]` includes an `excerpt` (sentence span) for each citation used in the answer.
- Optionally add a structured `supporting_spans[]` per citation (future): `{excerpt, char_start?, char_end?, confidence?}`.

**Tighten “activity-bearing evidence” (for `person_activity`):**
Minimum bar for `answerable=true` should be testable:
1. At least one citation excerpt includes an activity predicate (working on / leading / announced / building / proposal / etc.), AND
2. The evidence is within the recency window used, AND
3. Prefer evidence where the person is the actor/author (post author / PR author) vs being merely mentioned (when that metadata exists).

### Workstream B — Corpus Hygiene (Medium Priority)

**Goal:** reduce future “internal artifact” leakage by tagging/splitting corpora.

Options:
- Add doc metadata tags at ingestion: `visibility=public|internal`, `source_kind=forum|web|github|notion|social|ops`, `doc_kind=post|article|markdown|code|plan|dump|issue|pr|release_note`, `repo=<name>`.
- Exclude internal corpora by default in the Custom GPT query path (via `source_policy=public`).
- Keep internal corpora queryable for engineers via MCP tools / internal UI.

### Workstream C — Regression Tests + Metrics (Required for shipping A/B)

Add a small, repeatable test suite that measures “mention ≠ evidence” regressions:

**Test prompt families (intent buckets):**
- `person_activity`: “What is PERSON working on?”
- `person_bio`: “Who is PERSON?”
- `concept_explain`: “What is CONCEPT?”
- `technical_howto`: “How does SYSTEM work?”

**Metrics:**
- **Source hygiene rate**: % of citations that meet policy (no disallowed repos; human-facing URLs where expected).
- **Evidence support rate**: % of substantive claims that have at least one citation excerpt containing activity-bearing language (for `person_activity`).
- **Internal leakage rate**: % of answers that cite internal/infrastructure docs when `source_policy=public`.

**Definition of done (for A1 + A2 initial ship):**
- For `intent=person_activity` + `source_policy=public`, internal/infrastructure repos are excluded by default (configurable).
- Citations returned are relevant to activity, not only identity mentions; when evidence is weak, `answerable=false` is returned and the caller must not fabricate activity.
- Add at least one regression test case covering the “alias table / planning doc” false-positive pattern.

**Regression suite must include negative cases:**
- Relevant docs exist but only support identity (alias tables, rosters) → expect `answerable=false` and no activity claims.

### Suggested Implementation Sequence (MVP → Strong)

| Phase | Components | Why |
|------:|------------|-----|
| 1 | A1 (retrieval profile selection) + A2.1 (answerability) + recency window | Stops “grounded but wrong” narratives quickly |
| 2 | A1.1 taxonomy + heuristic backfill/inference | Makes filtering durable and less brittle |
| 3 | A4 supporting snippets (tighten excerpts) | Makes “mention ≠ evidence” mechanically enforceable |
| 4 | A3 evidence-type classifier (hybrid/LLM) | Improves precision beyond heuristics |

---

## Reports

- TBD
