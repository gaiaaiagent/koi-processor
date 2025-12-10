# Complete yonearth-gaia-chatbot Quality & Dedup Pipeline Analysis

## Executive Summary
- yonearth uses a layered pipeline: early vague/entity blocking → enrichment/resolution → normalization → deduplication (string + semantic) → confidence filtering.
- Dedup is handled in multiple places: `entity_deduplicator.py` (case-insensitive longest-name merge on relationship endpoints), `entity_resolver.py` (deterministic canonical selection), `graph_builder.py` (type-grouped fuzzy match + merge), `semantic_deduplicator.py` (embedding-based rel dedup), and `deduplicator.py` (exact rel dedup).
- Quality controls include ContextEnricher + VagueEntityBlocker pairing, EnhancedListSplitter for entity lists, predicate/type validators, and a strong EntityMergeValidator with semantic/blocklist vetoes.
- Pipelines are predefined for books/podcasts; book pipeline versions (v14.x) reorder modules for “resolve then block” and add book-specific filters (praise quotes, bibliographic parsing, figurative language).
- Key tunables: fuzzy thresholds (graph builder 90; entity_resolver 0.8; semantic_deduplicator 0.87), confidence thresholds from YAML, vague specificity 0.90, list splitting protections, and merge blocklists.

## Modules Discovered (core quality/dedup/resolution)
- `src/knowledge_graph/postprocessing/universal/entity_deduplicator.py` — normalizes entity names, merges variations on relationships (longest name wins, flags original source/target).
- `.../universal/entity_resolver.py` — deterministic canonicalization with tie-break: longest → most frequent → earliest; substring/word-overlap variant detection; alias map persisted; priority 112.
- `.../universal/deduplicator.py` — exact relationship dedup on normalized (source, rel, target); optional case/whitespace normalization; priority 110.
- `.../universal/semantic_deduplicator.py` — optional sentence-transformer similarity (default threshold 0.87, `all-MiniLM-L6-v2`); groups by source; keeps highest `p_true`; flags kept rels; fallback string matching if embeddings unavailable; priority 115.
- `.../universal/pronoun_resolver.py` — subject + possessive pronoun resolution with generic mappings, multi-pass (sentence/paragraph), context window (default 5 sentences, 1000 chars), author context, possessive cache, skip-pronoun types to avoid false positives; versions 1.0–1.6 noted.
- `.../universal/context_enricher.py` — resolves vague entities via keyword/context replacements and document-specific maps; runs before blocking.
- `.../universal/vague_entity_blocker.py` — specificity scoring (threshold 0.90), pattern list for vague/unknown terms, respects ContextEnricher flags, avoids blocking quotes; moved later in pipeline to “resolve then block.”
- `.../universal/list_splitter.py` — relationship-target splitter; `enhanced_list_splitter.py` handles entity-level splitting with protections for credentials, city/state, compound terms.
- `.../universal/predicate_normalizer.py` / `predicate_validator.py` / `type_compatibility_validator.py` — normalize predicates, validate logical compatibility, auto-fix type mismatches (tracks flags).
- `.../universal/claim_classifier.py` — classifies relationships (factual/philosophical/opinion/recommendation) to feed confidence thresholds.
- `.../universal/field_normalizer.py` / `market_stat_normalizer.py` — field/predicate cleanup prior to other modules.
- `.../universal/confidence_filter.py` — loads thresholds from `config/filtering_thresholds.yaml`; base 0.5 with flag-specific overrides; raises thresholds for unresolved pronouns.

### Validators & Resolvers
- `src/knowledge_graph/validators/entity_quality_filter.py` — stop-word, numeric, tautological, sentence-like, and length filters with stats.
- `src/knowledge_graph/validators/entity_merge_validator.py` — fuzzywuzzy-based merge guard with strict thresholds (95+), type constraints, length ratio checks, abbreviation expansion, country synonym map, and extensive semantic/blocklists to veto known bad merges.

### Graph Construction Layer
- `src/knowledge_graph/graph/graph_builder.py` — groups entities by type, fuzzywuzzy ratio threshold default 90; optional `EntityMergeValidator` and type-strict mode; merges metadata, rewrites relationships to canonical names via `entity_id_mapping` and `name_canonical_map`.
- `scripts/build_unified_graph_hybrid.py` — hybrid builder combining ACE postprocessed episodes + fresh book extractions; uses type normalization map, GraphSAGE embeddings (if present) for dedup tie-breakers, and `EntityMergeValidator`.

### Content-Specific (books)
- `postprocessing/content_specific/books/*` — praise quote detector, metadata/front-matter filters, dedication/subjective/narrative filters, bibliographic citation parser, author placeholder resolver, title completeness validator, figurative language filter, statement conciseness normalizer.

## Pipeline Architecture
- Base orchestrator (`postprocessing/base.py`): modules declare priority, dependencies, content types; orchestrator sorts and runs modules on relationship batches with stats.
- Book pipeline (`postprocessing/pipelines/book_pipeline.py`):
  - v14.3.4 (latest): FieldNormalizer → book-specific detectors/filters → ContextEnricher → ListSplitter (quote-aware) → PronounResolver → PredicateNormalizer/Validator → VagueEntityBlocker → Title/Figurative filters → ClaimClassifier → Deduplicator. (SemanticDeduplicator present in header list but not in v14.3.4 branch; present in earlier v14).
  - Earlier v14/v14.1: includes SemanticDeduplicator + ConfidenceFilter; v14.3.x emphasizes resolve-before-block and book-specific corrections.
- Podcast pipeline: VagueEntityBlocker → ListSplitter → ContextEnricher → PronounResolver → PredicateNormalizer → PredicateValidator (no book-specific modules, no semantic dedup by default).
- Entity/relationship validation occurs again in graph builders (fuzzy merge + merge validator) before final graph write.

## Key Algorithms (why/what)
- **Deterministic canonical choice** (`entity_resolver.py`): longest name preferred to avoid truncation; fallbacks to frequency and first occurrence to keep reproducibility across runs.
- **Fuzzy dedup** (`graph_builder.py`): type-aware grouping + fuzzywuzzy ratio (≥90) reduces false merges; optional type strictness and validator guard against homonyms (e.g., “Mars” vs “Paris”).
- **Semantic rel dedup** (`semantic_deduplicator.py`): cosine similarity groups paraphrased triples; keeper chosen via highest `p_true`, flags mark dedup groups.
- **Pronoun resolution**: multi-pass context windows, possessive handling, generic pronoun remapping for “you/we” instructional text, cache for possessive resolutions; skip certain entity types to avoid entity names containing pronouns.
- **Vague handling**: ContextEnricher attempts resolution before VagueEntityBlocker blocks; blocker uses specificity scoring, pattern lists, and respects ContextEnricher flags to avoid over-blocking.
- **Merge safety**: EntityMergeValidator uses blocklists, length ratios, abbreviation expansion, country synonyms, and flexible vs strict thresholds by type; prevents catastrophic merges seen historically.
- **List splitting**: relationship-level splitting handles and/&, comma patterns; entity-level enhanced splitter protects honorifics, city/state, credentials, and compound idioms.
- **Confidence filtering**: config-driven thresholds with flag-aware overrides (higher thresholds for metaphors/philosophical claims/unresolved pronouns).

## Configuration & Tunables
- Fuzzy thresholds: graph_builder similarity_threshold default 90; entity_resolver similarity_threshold 0.8; semantic_deduplicator similarity_threshold 0.87.
- Deduplicator settings: case_sensitive + whitespace normalization flags.
- VagueEntityBlocker specificity threshold 0.90; configurable vague patterns list.
- PronounResolver: `context_window` default 5, `resolution_window` 1000 chars, possessive pronoun list, skip types list.
- ConfidenceFilter: YAML `config/filtering_thresholds.yaml` controls base and flag-specific thresholds; unresolved pronoun flags force ≥0.7.
- Merge validator: STRICT_TYPES vs FLEXIBLE_TYPES thresholds; semantic blocklists; abbreviation map and stop titles.
- Pipeline versions toggled via `get_book_pipeline(version=...)`; module configs passed per-stage.

## Edge Cases Handled
- Possessive pronouns (“my people”) with caching and context extraction.
- Vague demonstratives (“this”, “that”, “unknown”, “community activities”) resolved or blocked with specificity scoring.
- Protected list splitting patterns for credentials and city/state strings.
- Merge veto for known bad pairs (e.g., “Moscow”≠“Soil”, “earth”≠“mars”, “dia”≠“sun”).
- Confidence threshold bump when pronouns unresolved; avoids low-confidence pronoun links.
- Predicate/type compatibility validator fixes or flags mismatches; avoids nonsensical edges.

## Dependencies & Infrastructure
- Libraries: `fuzzywuzzy` + `python-Levenshtein`, `sentence-transformers` (optional), `yaml`, `numpy`, Neo4j client in graph builder, GraphSAGE embeddings for hybrid builder tie-breaks.
- Config files: `config/filtering_thresholds.yaml`; GraphSAGE embeddings at `data/graphrag_hierarchy/*`.
- Pipelines are Python-native (no JSON config); scripts orchestrate extraction + graph build.

## Performance Characteristics
- String-based dedup (entity_deduplicator/entity_resolver) is O(n²) within type groups but mitigated by grouping and early checks; suitable for moderate batches.
- SemanticDeduplicator loads embedding model once; grouping by source reduces pairwise comparisons; falls back to string matching if model unavailable.
- Graph builder dedup groups by type and uses validator to short-circuit rejects; still quadratic per type bucket.

## Known Limitations / Observed Gaps
- Cross-document canonical registry is not global; relies on in-batch fuzzy matching and deterministic selection—risk of drift across runs.
- Semantic dedup depends on sentence-transformers being installed; otherwise falls back to simpler string matching.
- Pronoun resolution is heuristic and English-centric; no gender/number agreement check beyond patterns, limited multi-language support.
- Relationship conflict resolution is minimal (no temporal disambiguation or contradiction handling).
- Entity_quality filtering in validators is simpler than VagueEntityBlocker/ContextEnricher combo; stop-word set omits some Regen-specific generics (validators, tokens, etc.).
- Pipeline versions diverge: latest book pipeline omits SemanticDeduplicator/ConfidenceFilter in v14.3.4 branch (header mentions it but branch doesn’t include), so semantic dedup may not run unless earlier version selected.
