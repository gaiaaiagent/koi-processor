# B5.5 — Eval Taxonomy & Labeling Rubric

**Goal:** Expand golden QA from 20 → 100 questions with category-level measurement.

## 1. Categories

Aligned with B9a QueryTaxonomy for direct router evaluation.

| Category | Target Count | QueryTaxonomy mapping | Description |
|----------|-------------|----------------------|-------------|
| `entity_definition` | 20 | entity_definition | "What is X?" — single entity lookup |
| `relationship` | 15 | relationship_path | "How does X relate to Y?" — multi-entity, edges |
| `multi_hop` | 15 | relationship_path | "What practices does org X in bioregion Y use?" — 2+ hops |
| `governance` | 10 | governance_policy | "What is BKC's policy on X?" — protocol/governance docs |
| `roadmap_status` | 10 | roadmap_status | "What is the status of X?" — operational state |
| `commitment_claim` | 10 | commitment_claim | "What commitments exist for X?" — claims/commitments lifecycle |
| `thematic` | 10 | entity_definition or governance_policy | Cross-cutting themes |
| `negative` | 10 | out_of_domain | Out-of-domain → abstention |

**Total: 100 questions**

## 2. Coverage Targets by Content Domain

| Domain | Target % | Source |
|--------|----------|--------|
| Salish Sea ecology/species | 20% | Wiki corpus (~52% of chunks) |
| BKC governance/protocols | 20% | Doc chunks (~12% of chunks) |
| Organizations/people/relationships | 15% | Entity registry |
| Commitment economy/flow funding | 15% | Commitment/claim data + docs |
| Roadmap/project status | 10% | Operational data |
| Indigenous knowledge/cultural | 10% | Wiki + thematic docs |
| Out-of-domain | 10% | N/A (should trigger abstention) |

## 3. Question Format

```json
{
  "id": "entity_definition_7",
  "category": "entity_definition",
  "question": "What is Pacific herring?",
  "expected_answer": "Concise factual answer, 1-3 sentences, grounded in Octo corpus.",
  "expected_entities": ["Pacific Herring"],
  "gold_evidence": [
    "Pacific herring (Clupea pallasii) are a forage fish species..."
  ],
  "difficulty": "easy",
  "source_domain": "wiki",
  "notes": "Entity exists in Octo entity_registry. Wiki page imported."
}
```

### Field definitions

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `id` | Yes | string | Unique ID: `{category}_{N}` |
| `category` | Yes | string | One of the 8 categories above |
| `question` | Yes | string | Natural language question as a user would ask |
| `expected_answer` | Yes | string | Gold standard answer, 1-3 sentences |
| `expected_entities` | Yes | string[] | Entity names that MUST appear in retrieval context |
| `gold_evidence` | No* | string[] | 1-3 verbatim/near-verbatim text snippets from chunks/entities |
| `difficulty` | No* | string | `easy` \| `medium` \| `hard` |
| `source_domain` | No* | string | `wiki` \| `doc` \| `entity` \| `commitment` \| `roadmap` \| `mixed` |
| `notes` | No | string | Labeling context |

*Required before "B5.5 complete" but optional in seed batch.

## 4. Difficulty Levels

| Level | Distribution | Criteria |
|-------|-------------|----------|
| `easy` | ~40% | Single entity lookup; answer in entity description or single chunk |
| `medium` | ~40% | Requires relationship traversal or multi-chunk synthesis |
| `hard` | ~20% | Multi-hop reasoning, cross-source synthesis, or subtle domain knowledge |

## 5. Labeling Rules

1. Every `expected_answer` must be answerable from Octo's actual corpus — no aspirational answers.
2. `expected_entities` lists entities that MUST appear in retrieval context, not just the answer text.
3. `gold_evidence` contains verbatim or near-verbatim text from chunks/entities that an ideal retriever would find. Optional for seed batch, required before B5.5 is "complete."
4. `negative` category answers must reference BKC's domain scope (not just "I don't know").
5. Questions should be phrased as a user would ask Octo, not as a test author.
6. Avoid questions that test LLM general knowledge rather than retrieval quality.
7. For `commitment_claim` and `roadmap_status`: questions should target data that actually exists in the backend, not aspirational features.

## 6. Evidence Recall Metric

`gold_evidence` enables a new metric alongside DeepEval's existing three:

- **Evidence recall** = (gold_evidence snippets found in retrieval_context) / (total gold_evidence snippets)
- Matching: case-insensitive substring containment with 80% overlap threshold
- Questions without `gold_evidence` (null/empty array) skip this metric
- Complements DeepEval's context_relevancy (precision: "is retrieved stuff relevant?") with recall ("did we retrieve what we should have?")

## 7. Migration from Current 20-Question Set

The existing 20 questions keep their current `category` and `id` values. No renaming.

### Category normalization for reporting

`run_eval.py` gains a `taxonomy_map` that normalizes old categories for category-level reporting:

```python
TAXONOMY_MAP = {
    # Category-level defaults
    "entity_lookup": "entity_definition",
    "relationship": "relationship_path",
    "multi_hop": "relationship_path",
    "negative": "out_of_domain",
    # New categories (identity mapping)
    "entity_definition": "entity_definition",
    "governance": "governance_policy",
    "roadmap_status": "roadmap_status",
    "commitment_claim": "commitment_claim",
    "thematic": "entity_definition",  # default for thematic
}

# Per-question overrides for thematic (where category default is wrong)
TAXONOMY_OVERRIDES = {
    "thematic_2": "governance_policy",  # "How does BKC handle data sovereignty?"
}
```

### Backfill plan

Old questions get `gold_evidence`, `difficulty`, `source_domain` backfilled incrementally — not required immediately. New questions use the full format from the start.

## 8. Exit Criteria — "B5.5 ready enough to gate Phase 2"

1. **100 questions defined**: All categories filled per taxonomy targets.
2. **Gold evidence labeled**: At least 50/100 have `gold_evidence` populated.
3. **Baseline run complete**: All 100 scored against current `/chat` (no planner) on Octo.
4. **Category-level baselines**: CR, AR, faithfulness broken out by taxonomy category.
5. **Difficulty distribution**: ~40% easy, ~40% medium, ~20% hard (± 5%).
6. **Source domain distribution**: Matches corpus composition.
7. **Eval runner updated**: `run_eval.py` supports category-level reporting, taxonomy_map, and evidence recall scoring.
