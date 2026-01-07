# Regen Network Knowledge Graph Quality Review - Cycle 2026-02

**Started:** 2026-01-03
**Last Updated:** 2026-01-05
**Status:** Execution — retrieval profiles shipped; plan authored-entity graph links; added Workstream E (KG extraction chunking)
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

### Workstream D — Author Entities + Authorship Edges in the Graph (High Leverage)

**Problem:**
Authors are currently stored as strings in `koi_memories.metadata` (e.g., `author`, `author_username`, `author_id`) and are queryable in Postgres, but they are **not modeled as `PERSON` entities** in the knowledge graph and are **not linked** to the content they created (no `authored`/`posted_by` edges in the RDF graph).

This creates a gap:
- ✅ Postgres: can query authored content (e.g., “all Discourse posts by `Gregory_Regen`”).
- ❌ Graph (Fuseki): cannot query/traverse authorship (e.g., SPARQL `?person :authored ?post`), and can’t unify identities across sensors.

**Goal:**
Make authorship graph-queryable and deduplicatable across sources, without leaking internal/private corpora.

**MVP scope (recommended order):**
1. **Forum (Discourse) authorship edges**
   - Create/ensure a `PERSON` entity for each Discourse author (keyed by stable identifiers like `forum_host + user_id` and/or `author_username`).
   - Add an authorship relationship between the person and the content (choose one canonical predicate direction and stick to it):
     - Option A: `PERSON --authored--> PUBLICATION`
     - Option B: `PUBLICATION --posted_by--> PERSON`
   - Only emit these edges for `visibility=public` sources.
2. Expand to other public sources where authors are meaningful:
   - GitHub: `doc_kind=issue|pr|release_note` (prefer metadata about authorship; avoid raw code as activity evidence).
   - Social: Twitter/other public posts where username is stable.

**Backfill note (prerequisite for Discourse MVP):**
Older Discourse posts ingested via the semantic bridge may be missing author fields. Use the prod-safe backfill to populate `metadata.author*`:
- `koi-processor/scripts/backfill-discourse-authors.ts` (allowlisted Discourse API only)

**Benefits:**
- SPARQL queries like “find all posts authored by X”, “who are the most active authors in the last N months”, “which orgs are authors associated with”, etc.
- Cross-source identity resolution (Discourse username ↔ Twitter handle ↔ Notion user) via canonical entity merges.
- Cleaner `person_activity` answers: authored edges become first-class evidence instead of heuristic-only metadata matching.

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
| 1 | A1 (retrieval profile selection) + A2.1 (answerability) + recency window | Stops "grounded but wrong" narratives quickly |
| 2 | A1.1 taxonomy + heuristic backfill/inference | Makes filtering durable and less brittle |
| 3 | A4 supporting snippets (tighten excerpts) | Makes "mention ≠ evidence" mechanically enforceable |
| 4 | A3 evidence-type classifier (hybrid/LLM) | Improves precision beyond heuristics |

### Workstream E — KG Extraction Chunking for Large Documents (Medium Priority)

**Problem observed (2026-01-05):**
Large documents (100k+ chars) sent to KG extraction are failing to produce meaningful results and cause blocking issues:

| Content Size | Tokens Consumed | Entities | Statements | Notes |
|-------------|-----------------|----------|------------|-------|
| 133,408 chars | 39,515 | 0 | 0 | koi-query-api.ts |
| 108,127 chars | 23,483 | 0 | 0 | content_dashboard |
| 94,841 chars | 18,788 | 6 | 6 | weekly_curator |

**Document size distribution in corpus:**

| Size Bucket | Count | Extracted | Not Extracted |
|------------|-------|-----------|---------------|
| < 5k chars | 38,287 | - | - |
| 5k-20k chars | 1,943 | - | - |
| 20k-50k chars | 168 | - | - |
| 50k-100k chars | 62 | 28 | 34 |
| > 100k chars | 33 | 2 | 31 |

**Root causes:**
1. **No chunking logic exists** in either `PassAExtractor` or `UnifiedExtractor` - full content is sent to the LLM
2. **LLMs struggle with very long content** - possibly losing focus, hitting context limits, or failing to find structured patterns in large code files
3. **Blocking issues** - long OpenAI calls block the event loop (fixed 2026-01-05 by switching to async client)
4. **Chunk sizing is char-based (not token-based)** - extraction budgets need to match the deployed model’s context window and reserve output tokens

**Guiding principle:**
- Chunk by **tokens** (model-context aware) but split on **natural boundaries** (headings/paragraphs) to preserve meaning.
- Attach per-chunk metadata (`chunk_index`, `char_start`/`char_end`, `source_tokens`) so provenance + debugging remain clean.

**Trade-offs to consider:**
- **Larger chunks = more context** - helpful for resolving pronouns ("he" → named entity earlier in text), understanding relationships across paragraphs
- **Smaller chunks = better focus** - LLM may extract more reliably from focused content, less "lazy extraction" where model skips the middle
- **Chunk overlap** - needed to avoid losing entities/relationships at chunk boundaries
- **Tokens vs. chars** - token counts vary wildly across code/markdown/transcripts; use token budgets for safety but keep char offsets for provenance/excerpts
- **Global Consistency vs. Local Precision (Identity Problem):**
  - Large chunks: LLM sees "John" and "Mr. Smith" are the same person, creates one node
  - Small chunks: Chunk A sees "John", Chunk B sees "Mr. Smith" → two separate nodes requiring post-hoc entity resolution
  - Trade-off: Smaller chunks require smarter Entity Resolution/Merger step (we have this: `entity_registry` + `canonical_entities.json`)
- **Recall vs. Cost:**
  - Smaller chunks = More LLM calls = Higher cost
  - But also: Smaller chunks = Significantly higher recall (captures minor entities skipped in large-context summaries)
  - Need to find the "density sweet spot" where entity yield per dollar is maximized

**Investigation plan:**

1. **Baseline measurement** - For a sample of 50k+ char **non-code** documents (YouTube transcripts, forum posts, markdown docs), record current extraction results (entities, statements, confidence)

2. **Test multiple chunk sizes** - Re-extract the same documents with different chunk configurations:
   - No chunking (baseline - expected to fail, but needed for comparison)
   - **Token-based windows** (use the same tokenizer/model as extraction):
     - 4k source tokens with 400-token overlap (large-window test)
     - 2k source tokens with 200-token overlap (balanced)
     - 1k source tokens with 150-token overlap (high-focus)
     - 500 source tokens with 75-token overlap (max recall / high cost)
   - **Boundary strategy**: compare naive sliding-window vs. paragraph/heading-aware packing (same token budgets)

3. **Compare results** - For each configuration, measure:
   - **Entity Density**: entities extracted per 1k tokens of source text
   - **Unique entities after resolution**: how many survive dedup/merge
   - **Relationship density**: relationships per unique entity
   - **Identity fragmentation rate**: how often the same real-world entity appears as multiple nodes before resolution
   - Token usage and cost per document

4. **Edge case analysis** - Test pronoun resolution and cross-chunk context:
   - Does "he" in chunk 2 correctly resolve to "Gregory Landua" from chunk 1 (with overlap)?
   - Are relationships spanning chunk boundaries captured?

5. **Context Injection experiment** - Test passing a "previous chunk summary" into subsequent chunks:
   ```
   Context from previous section:
   - Entities mentioned: Gregory Landua (PERSON), Regen Network (ORG)
   - Active topic: carbon credit methodology

   Now extract entities and statements from this section:
   {chunk_content}
   ```
   Compare entity coherence vs. overlap-only approach.

**Implementation (after investigation):**

```python
# Proposed KG-extraction chunking config (values TBD; token-based)
MAX_SOURCE_TOKENS = 2_000          # <= send whole doc to extractor
TARGET_CHUNK_TOKENS = 1_000        # source tokens per chunk
CHUNK_OVERLAP_TOKENS = 150         # overlap for continuity
MAX_CHUNKS_PER_DOC = 64            # hard guardrail for runaway cost

async def chunk_and_extract(content: str, extractor) -> Dict:
    # Token counts must match the tokenizer/model used by the extractor.
    if count_tokens(content) <= MAX_SOURCE_TOKENS:
        return await extractor.extract(content)

    chunks = create_boundary_aware_chunks(
        content,
        target_tokens=TARGET_CHUNK_TOKENS,
        overlap_tokens=CHUNK_OVERLAP_TOKENS,
        max_chunks=MAX_CHUNKS_PER_DOC,
    )  # yields objects with: text, chunk_index, char_start, char_end, source_tokens

    all_entities = []
    all_statements = []
    context_summary = None  # optional: keep tiny, entity-only memory

    for chunk in chunks:
        # Optional: inject context from previous chunk (entity names only)
        prefix = f"Context: {context_summary}\n\n" if context_summary else ""
        result = await extractor.extract(prefix + chunk.text)

        # IMPORTANT: preserve chunk provenance for linking + debugging
        for e in result["entities"]:
            e["extraction_chunk_index"] = chunk.chunk_index
            e["extraction_char_start"] = chunk.char_start
            e["extraction_char_end"] = chunk.char_end
        for s in result["statements"]:
            s["extraction_chunk_index"] = chunk.chunk_index
            s["extraction_char_start"] = chunk.char_start
            s["extraction_char_end"] = chunk.char_end

        all_entities.extend(result['entities'])
        all_statements.extend(result['statements'])

        # Build context summary for next chunk (entity names only)
        if result['entities']:
            entity_names = [e['name'] for e in result['entities'][:5]]
            context_summary = f"Entities mentioned: {', '.join(entity_names)}"

    # IMPORTANT: Entity Resolution, not just string deduplication
    # Feed into existing pipeline: entity_registry + canonical_entities.json
    return resolve_and_merge(all_entities, all_statements)
```

**Entity Resolution pipeline (existing infrastructure):**
- `entity_registry.normalized_text` - fuzzy matching via B-tree index
- `canonical_entities.json` - alias → canonical mappings
- `scripts/apply_alias_merges.py` - merge duplicates with relationship migration
- Vector similarity fallback for semantically similar but lexically different entities

**Provenance/linking note:**
KG extraction chunk sizes do NOT need to match embedding chunk sizes. The `koi_entity_chunk_links` table links entities back to source documents via `chunk_rid` for retrieval purposes. As long as we maintain the source document RID in the extraction metadata, provenance is preserved regardless of extraction chunk size.

**Success criteria:**
- Large **non-code** documents (50k+ chars) produce meaningful extractions (>0 entities; ideally >10 after merge on the benchmark set)
- **Entity Density**: maximize entities per 1k tokens (target: find the "density sweet spot")
- **Identity Stability**: entities mentioned across chunks resolve correctly (e.g., "Gregory Landua" in chunk 1 connects to "he" or "Greg" in chunk 2)
- **Cost efficiency**: entity yield per dollar is reasonable (avoid 40k token extractions that yield 0 entities)
- No extraction blocking issues (async client already fixed 2026-01-05)
- Clear documentation of optimal chunk size based on empirical testing

**Code file handling (separate pipeline):**
Code documents are handled by the code pipeline (Tree-sitter/AST), not the LLM KG extractor. Therefore:
- Skip LLM KG extraction/chunking entirely for `doc_kind=code` (return empty extraction + log)
- Optional: if you want cross-linking between code and the main KG, extract only docstrings/comments into the same entity space (strict token budget)

**Files to modify:**
- `koi-sensors/knowledge_graph/extractors/pass_a_extractor.py` - real-time extraction
- `koi-sensors/knowledge_graph/extractors/unified_extractor.py` - bulk extraction
- `koi-sensors/knowledge_graph/scripts/bulk_extract.py` - batch processing

---

## Reports

- TBD
