# Complete Deduplication & Quality Implementation Plan

## Executive Summary
Build a Regen-focused, cross-document deduplication and quality stack that combines yonearth’s proven modules with Regen-specific aliasing, stronger relationship validation, and persistent canonical registries. Prioritize quick dedup wins (fuzzy + registry) while setting up infrastructure for semantic/temporal resolution and conflict handling.

## Phase 1: Core Deduplication (MUST HAVE)
1.1 Cross-Source Canonical Registry  
- Persist canonical entities + aliases across runs (DB/JSON).  
- Seed with Regen-specific aliases (Regen Network/RND/Regen Ledger/$REGEN, ecocredit module, key people).  
- API for lookup/update during pipeline and graph build.

1.2 Fuzzy Entity Dedup (type-aware)  
- Adapt yonearth `entity_resolver`/graph_builder fuzzy logic with stricter validator for blockchain terms.  
- Threshold tuning per type; guard with merge validator blocklists + abbreviation/iso maps.

1.3 Relationship Dedup (exact + semantic)  
- Exact dedup of (source, predicate, target).  
- Semantic dedup (embeddings) with per-predicate thresholds; fall back to string if model unavailable.

1.4 Entity Quality Filter Upgrade  
- Expand stop/generic lists with Regen roles (“validator”, “delegator”, “community member”, “unknown”, “team”).  
- Add code/handle patterns (GitHub usernames, file paths, PR/issue IDs).

1.5 Pipeline Wiring & Metrics  
- Insert dedup + quality modules in Regen pipeline (resolve → block → dedup → semantic dedup → confidence).  
- Track stats: merges, blocked reasons, unresolved pronouns, semantic groups, registry hits/misses.

## Phase 2: Advanced Resolution (SHOULD HAVE)
2.1 Pronoun & Possessive Resolver  
- Port yonearth pronoun resolver; add type-aware constraints (org vs person) and doc-author hints for GitHub issues.  
- Cache resolutions per doc; elevate confidence requirements when unresolved.

2.2 Acronym/Abbreviation Expansion  
- Build map (IBC, ICS, CRU, NCT, SDK versions); integrate into resolver before fuzzy matching.

2.3 Temporal/Version Handling  
- Normalize versioned entities (e.g., “SDK 0.47”) and support rebrand aliases with time ranges.

2.4 Context Enrichment for Technical Text  
- Adapt ContextEnricher patterns for code/docs (e.g., “this module”, “the token”, “the chain”); avoid blocking code identifiers.

## Phase 3: Relationship Quality (SHOULD HAVE)
3.1 Conflict Detection  
- Detect contradictory facts (e.g., two founded_dates) and mark conflicts for review.  
- Prefer higher-confidence + newest source.

3.2 Predicate Normalization for Blockchain  
- Map GitHub-phrased predicates to ontology (e.g., “opened PR”, “merged to”, “validators run”, “governed by”).  
- Validate type compatibility (person vs repo vs module).

3.3 Relationship Dedup by Context  
- Incorporate doc/source IDs to avoid merging unrelated edges with identical labels.

## Phase 4: Domain-Specific Enhancements (NICE TO HAVE)
4.1 Blockchain Entity Model  
- Distinct types for Validator, Token, Module, Proposal, Repository; tailor dedup thresholds + stopwords.

4.2 Multi-Language Support  
- Light-weight language detection; bypass pronoun/vague blockers for non-EN or apply language-specific lists.

4.3 Graph-Assisted Resolution  
- Use embeddings/GraphSAGE to disambiguate short names (e.g., “Gregory” near Regen topics → Gregory Landua).

## Implementation Order (Impact vs Effort)
1) Phase 1.1–1.4 (registry + fuzzy dedup + quality list)  
2) Phase 1.5 (pipeline wiring/metrics)  
3) Phase 2.1 (pronoun resolver)  
4) Phase 2.2–2.3 (acronyms + temporal)  
5) Phase 3.x (conflict + predicate normalization)  
6) Phase 4 (domain/graph/ML enhancements)

## Testing Strategy
- Unit tests for dedup/merge validator with Regen fixtures (aliases, validators, module names).  
- Regression suite on sampled GitHub docs (pre/post dedup counts for Regen/validator/Gregory variants).  
- Property tests: no merge across types, conflict detector catches divergent dates.  
- Integration: pipeline run on 50-doc pilot; diff blocked/merged stats vs baseline.

## Rollback Plan
- Feature flags per module; keep previous pipeline config available.  
- Registry writes versioned; allow disabling semantic dedup if embeddings missing.

## Success Metrics
- ≥90% reduction in Regen/Gregory/validator duplicates on pilot set.  
- <5% of relationships flagged contradictory post-Phase 3.  
- Pronoun unresolved rate <10% with confidence gating.  
- Pipeline runtime increase <25% vs baseline on pilot batch.

## Dependencies
- `fuzzywuzzy`/`python-Levenshtein`; `sentence-transformers` for semantic dedup; lightweight DB/JSON for registry; optional GraphSAGE embeddings if available.

## Risks & Mitigations
- Over-merging short blockchain terms → mitigate with merge validator blocklists + type strict mode.  
- Embedding availability → fallback to string dedup with warning; gate by flag.  
- Registry drift → versioned updates + stats on hits/misses.  
- Performance → type-grouping + threshold ordering; batch size tuning.
