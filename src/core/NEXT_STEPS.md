# Next Steps for KOI Knowledge System

## Immediate Actions

### 1. Clean Up Apache Jena (HIGH PRIORITY)
```bash
# The old graph (428K triples) is slowing queries
# Archive full graph, then load only refined (20K triples)
python3 archive_and_deploy.py  # May need to fix timeout issue
```

### 2. Implement TRUE Hybrid Search
Current: SPARQL → fallback to vector
Target: SPARQL + vector simultaneously

```typescript
// regen-koi-mcp needs:
async hybridQuery(query: string) {
  const [sparqlResults, vectorResults] = await Promise.all([
    this.sparqlClient.queryGraph(query),
    this.searchKnowledge(query)
  ]);

  return mergeAndRank(sparqlResults, vectorResults);
}
```

### 3. Add Semantic Predicate Matching
Currently using keyword matching for predicates.
Should use the 82.5MB embeddings we generated:

```python
# Use predicate_embeddings.pkl for semantic matching
# Already have embeddings for all 7,037 predicates
# Cost: $0.78 already spent!
```

## Architecture Alignment

### What We Built vs. What Was Planned

| Component | Planned (HYBRID_RAG.md) | Built | Gap |
|-----------|-------------------------|-------|-----|
| **Dual Paths** | Parallel processing | Sequential fallback | Need true parallel |
| **Entity Extraction** | LLM-based with ontology | Direct predicate consolidation | Working but different approach |
| **Embedding Integration** | For predicate matching | Keyword matching only | Have embeddings, not using |
| **Query Fusion** | Combine SPARQL + vector | Either/or fallback | Need result merging |

### Performance Impact

Current issues:
1. **428K triples** in Jena (should be 20K) → Slow queries
2. **No embedding use** for predicates → Less semantic understanding
3. **Sequential not parallel** → Higher latency
4. **No result fusion** → Missing cross-modal insights

## Recommended Priority

1. **Archive & reload Jena** (1 hour)
   - Fixes immediate performance issue
   - Already have refined graph ready

2. **Use predicate embeddings** (2 hours)
   - Load `predicate_embeddings.pkl`
   - Replace keyword matching with cosine similarity
   - Dramatic improvement in NL→SPARQL

3. **True hybrid search** (4 hours)
   - Parallel SPARQL + vector queries
   - Result fusion with scoring
   - Follows original architecture

## What's Working Well

✅ **Predicate Consolidation** - 7,037 → 4,009 (43% reduction)
✅ **Graph Refinement** - Removed 3K duplicates, CAT receipts
✅ **MCP Integration** - Enhanced SPARQL client deployed
✅ **Test Harness** - 20 queries for validation
✅ **Focused Retrieval** - 5-15 relevant predicates per query

## Summary

The system works but deviates from the planned hybrid architecture. The most impactful next step is cleaning Jena (remove 428K old triples, keep only 20K refined). Then leverage the $0.78 embeddings investment for semantic predicate matching. Finally, implement true parallel hybrid search as originally designed.

The consolidation work (0.25 threshold, 4,009 predicates) is solid and production-ready. Just need to complete the deployment and integration.