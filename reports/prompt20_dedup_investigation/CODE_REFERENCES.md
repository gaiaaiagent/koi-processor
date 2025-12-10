# yonearth-gaia-chatbot Code References (Dedup/Quality)

- `src/knowledge_graph/postprocessing/universal/entity_deduplicator.py` — relationship-endpoint dedup; longest-name canonicalization.
- `src/knowledge_graph/postprocessing/universal/entity_resolver.py` — deterministic canonical selection (longest → freq → earliest); alias map.
- `src/knowledge_graph/postprocessing/universal/deduplicator.py` — exact relationship deduplication.
- `src/knowledge_graph/postprocessing/universal/semantic_deduplicator.py` — embedding/string-based semantic relationship dedup (threshold 0.87 default).
- `src/knowledge_graph/postprocessing/universal/pronoun_resolver.py` — pronoun + possessive resolution with context windows and generic mappings.
- `src/knowledge_graph/postprocessing/universal/context_enricher.py` — vague entity replacement using keyword/context rules.
- `src/knowledge_graph/postprocessing/universal/vague_entity_blocker.py` — vague/unknown pattern blocking with specificity scoring.
- `src/knowledge_graph/postprocessing/universal/enhanced_list_splitter.py` & `list_splitter.py` — entity-level and relationship-level list splitting.
- `src/knowledge_graph/postprocessing/universal/predicate_normalizer.py`, `predicate_validator.py`, `type_compatibility_validator.py` — predicate/type cleanup and validation.
- `src/knowledge_graph/postprocessing/universal/confidence_filter.py` — flag-aware confidence thresholds (`config/filtering_thresholds.yaml`).
- `src/knowledge_graph/unified_builder.py`, `graph/graph_builder.py` — graph assembly, type-grouped fuzzy dedup, relationship rewrite.
- `src/knowledge_graph/validators/entity_merge_validator.py` — merge guard with blocklists, abbreviation map, strict thresholds.
- `src/knowledge_graph/validators/entity_quality_filter.py` — stop-word/numeric/tautology/sentence filters.
- `src/knowledge_graph/postprocessing/pipelines/book_pipeline.py`, `podcast_pipeline.py` — module ordering, book-specific modules, version options.
- `scripts/build_unified_graph_hybrid.py` — hybrid graph builder using GraphSAGE embeddings + merge validator.
- `scripts/deduplicate_entities.py` — standalone entity dedup over processed JSON with fictional registry handling.
