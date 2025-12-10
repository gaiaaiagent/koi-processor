# GitHub Sensors Research

**Date**: 2025-12-09
**Purpose**: Document differences between github-sensor and github-activity-sensor for entity extraction

---

## Sensor Comparison

### github-sensor
**Purpose**: Capture repository file contents

**Location**: `/Users/darrenzal/projects/RegenAI/koi-sensors/sensors/github/github_sensor.py`

**Content Types Captured**:
- Documentation: `.md`, `.mdx`, `.rst`, `.txt`, `README*`, `CHANGELOG*`, `CONTRIBUTING*`
- Source Code: `.go`, `.ts`, `.tsx`, `.js`, `.jsx`, `.py`, `.rs`, `.sol`
- Protocol Definitions: `.proto`
- Configuration: `.json`, `.yaml`, `.yml`, `.toml`, `.ini`, `.cfg`, `go.mod`, `go.sum`
- Build Files: `Makefile`, `Dockerfile*`, `docker-compose*.yml`
- Scripts: `.sh`, `.sql`, `.graphql`

**How It Works**:
1. Clones each configured repository (full clone for commit history)
2. Finds files matching `file_extensions` patterns
3. Filters out excluded directories (`node_modules`, `vendor`, `.git`, etc.)
4. Reads file content and creates documents
5. Extracts git commit metadata (author, date, message)
6. Sends to KOI coordinator as bundles

**Storage**: Files are chunked and stored in `koi_memories`
- Files are chunked by content size (MAX_FILE_SIZE = 500KB)
- RID format: `github_{repo_name}_{relative_path}` (slashes replaced with underscores)

**Repositories Monitored**:
- regen-ledger, regen-web, regen-data-standards, regen-registry-handbook
- regen-registry-methodology-library
- koi-sensors, koi-processor, koi-research, regen-koi-mcp

---

### github-activity-sensor
**Purpose**: Capture repository activity and communications

**Location**: `/Users/darrenzal/projects/RegenAI/koi-sensors/sensors/github_activity/github_activity_sensor.py`

**Content Types Captured**:
- **Commits**: SHA, message, author, date, stats
- **Issues**: Title, body/description, labels, comments count, state
- **Pull Requests**: Title, description, state, merged status, commits/additions/deletions
- **Discussions**: (Not yet implemented - requires GraphQL API)

**How It Works**:
1. Uses GitHub REST API to fetch activity from last `lookback_hours` (default: 24h)
2. Fetches commits, issues, and PRs in parallel
3. Creates documents with rich metadata
4. Tracks processed items to avoid duplicates
5. Sends to KOI coordinator as bundles

**RID Formats**:
- Commits: `github.commit:{owner}_{repo}_{sha}`
- Issues: `github.issue:{owner}_{repo}_{number}`
- PRs: `github.pr:{owner}_{repo}_{number}`

**Rate Limits**:
- Without token: 60 requests/hour
- With GITHUB_TOKEN: 5,000 requests/hour

---

## Entity Extraction Strategy

### github-sensor Documents

**Extract from** (markdown/documentation files):
- `.md`, `.mdx`, `README*` files - Primary documentation
- `.rst`, `.txt` files - Additional documentation
- `.asciidoc`, `.adoc` files - Asciidoc documentation

**Skip** (code/config files):
- Source code: `.go`, `.py`, `.ts`, `.js`, `.rs`, `.sol`
- Configuration: `.json`, `.yaml`, `.toml`, `go.mod`
- Protocol definitions: `.proto`
- Scripts: `.sh`, `.sql`, `.graphql`
- Build files: `Makefile`, `Dockerfile`

**Reason**:
- Code is processed separately by tree-sitter for structure analysis (functions, classes, imports)
- Entity extraction is designed for natural language text, not code
- Configuration files contain data, not entity-rich text

### github-activity-sensor Documents

**Extract from**:
- All documents (commits, issues, PRs)
- Content is already text-based
- Rich with entity mentions (people, organizations, projects, concepts)

**No filtering needed** - all activity content is appropriate for entity extraction

---

## Database Query: Identify Documents for Extraction

### Find GitHub Markdown Files Without Extractions

```sql
-- GitHub sensor markdown files without extractions
SELECT
    m.rid,
    m.source_sensor,
    m.content->>'title' as title,
    e.id as has_extraction
FROM koi_memories m
LEFT JOIN koi_kg_extractions e ON m.rid = e.memory_rid
WHERE
    m.source_sensor LIKE 'github-sensor%'
    AND (
        m.rid LIKE '%.md#%'
        OR m.rid LIKE '%.mdx#%'
        OR m.rid LIKE '%README#%'
        OR m.rid LIKE '%.rst#%'
        OR m.rid LIKE '%.txt#%'
        OR m.rid LIKE '%.asciidoc#%'
        OR m.rid LIKE '%.adoc#%'
    )
    AND e.id IS NULL
ORDER BY m.created_at DESC;
```

### Find GitHub Activity Documents Without Extractions

```sql
-- GitHub activity documents without extractions
SELECT
    m.rid,
    m.source_sensor,
    m.content->>'title' as title,
    e.id as has_extraction
FROM koi_memories m
LEFT JOIN koi_kg_extractions e ON m.rid = e.memory_rid
WHERE
    m.source_sensor LIKE 'github-activity-sensor%'
    AND e.id IS NULL
ORDER BY m.created_at DESC;
```

---

## Expected Results

| Source | Documents | Est. Entities | Content Type |
|--------|-----------|---------------|--------------|
| github-sensor (markdown) | ~428 | ~4,500-5,000 | Documentation, guides, READMEs |
| github-activity-sensor | ~23 | ~200-300 | Issues, PRs, commits |
| **Total** | **~451** | **~4,700-5,300** | Text-based |

---

## Implementation Notes

### File Extension Detection
RID format includes file path, so we can filter by extension:
- `github_regen_ledger_docs_README.md#chunk_0` - markdown file
- `github_regen_ledger_x_ecocredit_keeper.go#chunk_0` - Go file (skip)

### Markdown Detection Patterns
```python
markdown_patterns = [
    "%.md#%",
    "%.mdx#%",
    "%README#%",
    "%.rst#%",
    "%.txt#%",
    "%.asciidoc#%",
    "%.adoc#%"
]
```

### Code Detection Patterns (to skip)
```python
code_patterns = [
    "%.go#%",
    "%.py#%",
    "%.ts#%", "%.tsx#%",
    "%.js#%", "%.jsx#%",
    "%.rs#%",
    "%.sol#%",
    "%.proto#%",
    "%.json#%",
    "%.yaml#%", "%.yml#%",
    "%.toml#%",
    "%.sh#%",
    "%.sql#%"
]
```

---

## Conclusion

Both sensors have valuable text content for entity extraction, but serve different purposes:

1. **github-sensor**: Repository files
   - Extract from: Markdown/documentation files only
   - Skip: Code and configuration files
   - Reason: Code structure is handled by tree-sitter separately

2. **github-activity-sensor**: Activity/communications
   - Extract from: All activity (commits, issues, PRs)
   - No filtering needed
   - Rich with entity mentions

This separation ensures:
- Clean entity extraction from natural language text
- No noise from code/config files in entity graph
- Complementary coverage: file content + activity communications
