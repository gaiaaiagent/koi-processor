# Knowledge Graph Quality Review - Regen KOI Production Graph

## Mission

Perform a comprehensive quality audit of the Regen Network production knowledge graph stored in PostgreSQL/Apache AGE (database: `eliza`, port: 5433). Identify data quality issues, missing relationships, duplicates, and opportunities for improvement.

## Context

**Current Graph Statistics** (from ontology_summary.md):
- **23,273 Entities** (Entity class)
- **23,273 Statements** (relationships)
- **Top Entity Types**: Organization (8,192), Project (6,067), Activity (5,290), Person (3,549)
- **Top Predicates**: "is" (833), "provides" (774), "has" (364), "supports" (314)
- **4,401 Properties** across 15 classes

**Data Sources**: Discourse forums, Telegram, Twitter, Medium, podcasts, GitHub docs, Notion pages

**Production Database**:
- Host: localhost (or 202.61.196.119 if running remote analysis)
- Port: 5433
- Database: eliza
- User: postgres
- Password: postgres

**Access**: Database dump available at `/tmp/eliza_dump.backup` (651MB)

## Your Tasks

### 1. Graph Structure Analysis

Analyze the graph schema and structure:

**Deliverables**:
- [ ] Document the complete graph schema (vertex labels, edge labels, properties)
- [ ] Identify all relationship types and their frequency distribution
- [ ] Map entity type hierarchy and cardinality
- [ ] Generate statistics on graph connectivity (avg degree, isolated nodes, hub nodes)
- [ ] Identify any schema inconsistencies or non-standard structures

**Tools**:
```sql
-- Query Apache AGE graph
SELECT * FROM cypher('eliza', $$
  MATCH (n)
  RETURN labels(n), count(n)
$$) as (label agtype, count agtype);
```

### 2. Data Quality Issues

Identify and categorize quality problems:

#### 2.1 Entity Quality Issues

**Check for**:
- [ ] **Pronouns as entities**: "we", "she", "he", "they", "it", "I", "you"
- [ ] **Generic nouns**: "people", "person", "farmer", "teacher", "scientist"
- [ ] **Numeric-only entities**: "2030", "35", "1956"
- [ ] **Sentence fragments**: Entities longer than 100 characters or containing full sentences
- [ ] **Tautological entities**: Entity name matches type (e.g., "organization" with type ORGANIZATION)
- [ ] **Unsplit lists**: "Alice, Bob, and Carol" as single entity
- [ ] **Lowercase single-word PERSONs**: "mom", "dad", "friend"
- [ ] **Empty or whitespace-only entities**

**Deliverable**: CSV report with columns: `entity_id, entity_name, entity_type, issue_category, severity, suggested_action`

#### 2.2 Relationship Quality Issues

**Check for**:
- [ ] **Missing targets**: Relationships pointing to non-existent entities
- [ ] **Self-loops**: Entity related to itself
- [ ] **Duplicate relationships**: Same (subject, predicate, object) triple multiple times
- [ ] **Generic predicates**: Overuse of "is", "has", "related_to"
- [ ] **Inverse relationship gaps**: "A manages B" exists but not "B managed_by A"
- [ ] **Orphaned edges**: Edges with null/missing source or target

**Deliverable**: CSV report with columns: `relationship_id, subject, predicate, object, issue_category, severity, suggested_action`

#### 2.3 Duplicate Detection

**Check for**:
- [ ] **Exact duplicates**: Identical entity names (case-sensitive)
- [ ] **Case-insensitive duplicates**: "Regen Network" vs "regen network"
- [ ] **Fuzzy duplicates**: Similar names (85%+ similarity) - "Y on Earth" vs "YonEarth" vs "Y On Earth Community"
- [ ] **Alias opportunities**: Entities that should be merged with canonical forms
- [ ] **Cross-type duplicates**: Same name appearing as multiple entity types

**Deliverable**:
- Duplicate clusters JSON file with merge recommendations
- Entity merge mapping: `original_entity → canonical_entity`

### 3. Completeness Analysis

Identify missing data and gaps:

**Check for**:
- [ ] **Missing relationships**: Hub entities with suspiciously low connection counts
- [ ] **Asymmetric relationships**: Expected bidirectional links that are unidirectional
- [ ] **Missing entity attributes**: Entities lacking critical metadata (description, source, timestamps)
- [ ] **Data source coverage**: Which sources are over/under-represented
- [ ] **Temporal gaps**: Time periods with low entity/relationship coverage
- [ ] **Cross-domain connections**: Missing links between organizations, people, projects

**Deliverable**: Gap analysis report with prioritized recommendations

### 4. Provenance & Metadata Quality

**Check for**:
- [ ] **Unknown sources**: Entities without `hadPrimarySource` attribution
- [ ] **Missing confidence scores**: Statements lacking confidence values
- [ ] **Extraction metadata**: Entities missing `wasGeneratedBy` (extraction job info)
- [ ] **Timestamp accuracy**: Entities with impossible/future dates
- [ ] **Source URL validation**: Broken or malformed source URLs

**Deliverable**: Metadata quality scorecard with fix recommendations

### 5. Semantic Consistency

**Check for**:
- [ ] **Ontology violations**: Entity types or predicates not in allowed lists
- [ ] **Type mismatches**: Person entities with organization-style names
- [ ] **Predicate misuse**: "published" connecting non-document entities
- [ ] **Confidence anomalies**: Patterns of artificially high/low confidence scores
- [ ] **Contextual errors**: Entities extracted out of context (e.g., book characters marked as real people)

**Deliverable**: Ontology validation report with normalization suggestions

### 6. Hub Analysis & Network Topology

**Analyze graph centrality and importance**:

**Check for**:
- [ ] **Top 50 hub entities** by degree centrality (most connected)
- [ ] **Unexpected hubs**: Generic/low-quality entities with high connectivity
- [ ] **Isolated clusters**: Disconnected subgraphs
- [ ] **Bridge entities**: Nodes connecting otherwise separate communities
- [ ] **Community detection**: Identify major thematic clusters (Leiden/Louvain algorithm)

**Deliverable**:
- Network topology report with visualizations
- Ranked list of most important entities (filtered for quality)
- Community structure analysis

### 7. Data Utility Assessment

**Evaluate graph usefulness for RAG/search**:

**Check for**:
- [ ] **Search-unfriendly entities**: Too generic to be useful query targets
- [ ] **Under-connected concepts**: Important topics with too few relationships
- [ ] **Noise entities**: High-frequency, low-value nodes that pollute search results
- [ ] **Missing synonyms/aliases**: Entities lacking alternative name mappings
- [ ] **Relationship expressiveness**: Are predicates specific enough for semantic search?

**Deliverable**: RAG utility scorecard with improvement recommendations

## Deliverables Summary

Produce the following artifacts:

1. **Executive Summary** (2-3 pages)
   - Overall graph health score (0-100)
   - Top 10 critical issues
   - Recommended immediate fixes
   - Long-term improvement roadmap

2. **Detailed Reports** (CSV/JSON):
   - Entity quality issues (CSV)
   - Relationship quality issues (CSV)
   - Duplicate clusters (JSON)
   - Gap analysis (Markdown)
   - Metadata quality scorecard (JSON)
   - Ontology validation report (CSV)
   - Network topology analysis (JSON + visualizations)
   - RAG utility assessment (Markdown)

3. **Fix Scripts** (Python/Cypher):
   - Script to remove low-quality entities
   - Script to merge duplicate entities
   - Script to prune orphaned relationships
   - Script to normalize ontology types/predicates
   - Script to add missing inverse relationships

4. **Benchmarks** (JSON):
   - Current graph statistics (baseline)
   - Post-cleanup projected statistics
   - Quality metrics before/after

## Technical Requirements

**Environment**:
- Python 3.10+
- PostgreSQL client libraries (`psycopg2`)
- Apache AGE extension (`apache-age-python`)
- pandas, networkx, scipy (for analysis)

**Database Access**:
```python
import psycopg2
import age

conn = psycopg2.connect(
    host="localhost",
    port=5433,
    database="eliza",
    user="postgres",
    password="postgres"
)

# Query example
cursor = conn.cursor()
cursor.execute("SET search_path = ag_catalog, '$user', public;")
cursor.execute("""
    SELECT * FROM cypher('eliza', $$
        MATCH (n:Entity)
        RETURN n.name, n.type, id(n)
        LIMIT 10
    $$) as (name agtype, type agtype, id agtype);
""")
```

**Output Location**: Save all reports to `/Users/darrenzal/projects/RegenAI/koi-processor/reports/kg_quality_review_YYYYMMDD/`

## Success Criteria

- [ ] All 7 analysis tasks completed
- [ ] All deliverables produced and saved
- [ ] Executive summary clearly prioritizes top issues
- [ ] Fix scripts tested on sample data (not run on production without approval)
- [ ] Recommendations are actionable and specific
- [ ] Reports are well-formatted and easy to understand

## Timeline

Estimated: 4-6 hours for full analysis

## Notes

- **Do not modify production data** - only analyze and report
- If database is not accessible, work with the `/tmp/eliza_dump.backup` file by restoring to a local test database first
- Prioritize critical issues (data integrity) over nice-to-haves (cosmetic improvements)
- Use sampling for expensive queries (e.g., fuzzy duplicate detection on 23K entities)

## Questions for Clarification

Before starting, confirm:
1. Should analysis run on production DB (port 5433) or restore dump to local test DB first?
2. Are there specific entity types or relationships to focus on?
3. What is the threshold for "low quality" confidence scores?
4. Should fictional entities (e.g., from books/stories) be flagged or excluded?
