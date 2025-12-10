# Prioritized Modules for Regen Dedup & Quality

1) **Canonical Registry + Fuzzy Dedup (type-aware)**  
   - Build persistent alias registry; apply fuzzy merge per type with merge validator guard.  
   - Reference: `src/knowledge_graph/postprocessing/universal/entity_resolver.py`, `graph/graph_builder.py`.

2) **Entity Quality Filter Upgrade**  
   - Expand stop/generic lists for Regen (validators, delegators, unknown/team).  
   - Reference: `validators/entity_quality_filter.py`, `universal/vague_entity_blocker.py`.

3) **Relationship Dedup (exact + semantic)**  
   - Exact tuple dedup, plus embedding/string semantic dedup with per-predicate thresholds.  
   - Reference: `universal/deduplicator.py`, `universal/semantic_deduplicator.py`.

4) **Pronoun & Possessive Resolver (type-aware)**  
   - Port resolver; add constraints for org/person and doc-author hints.  
   - Reference: `universal/pronoun_resolver.py`.

5) **Acronym/Abbreviation Expansion**  
   - Map IBC/ICS/CRU/NCT/RND, SDK versions; integrate before dedup.  
   - Reference pattern: `validators/entity_merge_validator.py` (abbreviation map).

6) **Temporal/Version & Predicate Normalization (blockchain)**  
   - Normalize versioned entities; predicate mapper for code/governance events.  
   - Reference: `postprocessing/universal/predicate_normalizer.py`, `type_compatibility_validator.py`.

7) **Conflict Detection & Relationship Quality**  
   - Detect contradictory attributes (dates/orgs) and flag.  
   - Reference: Confidence/ClaimClassifier for gating thresholds.

8) **Graph-Assisted Resolution (optional)**  
   - Use GraphSAGE/embeddings for short-name disambiguation when available.  
   - Reference: `scripts/build_unified_graph_hybrid.py`.
