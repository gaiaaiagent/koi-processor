# Changelog

All notable changes to the KOI Processor project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [3.0.0] - 2025-12-23

### Added
- **Stage 6 Full Re-Extraction** - Complete docs-only corpus re-extraction using Gemini Flash
  - 12,002 documents processed
  - 88,322 entities extracted
  - 17,329 relationships extracted
  - 30,041 unique entities in entity_registry
- **FIX-007 Predicate Consolidation** - Reduced predicate sprawl from 3,303 to 1,501 distinct predicates (-54.6%)
- **Code↔Docs Bridge** - Full integration between documentation and code artifacts
  - 16,820 code artifacts exported
  - 6,453 doc→code links created
  - 241 entity→code links (API_MESSAGE, MODULE types)
- **AGE Graph Sync** - Apache AGE integration for graph database
  - 5,464 stub nodes (Person, Organization, CodeArtifact, Doc)
  - 6,463 edges (MENTIONS, CODE_REF)
- **PostgreSQL-based Fuseki Rebuild** - New rebuild pipeline from authoritative PostgreSQL data
- **Quality Gates** - Automated verification for production deployments
  - No http://regen.network/ URIs
  - No ontology# types/predicates
  - No self-referential triples

### Fixed
- **Predicate Format Violations** - All predicates now conform to `^[a-z0-9_]+$` pattern
- **HTTP URI Issues** - Eliminated all http:// URIs in favor of https://
- **Self-Referential Relationships** - Removed relationships where subject = object
- **AGE Sync Compatibility** - Fixed multi-label MERGE syntax and batch execution issues

### Changed
- **Extraction Model** - Switched from GPT-4.1-mini to Gemini Flash (99.4% vs 98.4% pass rate)
- **PostgreSQL as Authoritative Store** - Fuseki now rebuilt from PostgreSQL rather than dual-write
- **Entity Deduplication** - Improved from 70% to higher dedup rates with semantic matching

### Deployment
- **Production Fuseki**: 165,619 triples deployed to /koi endpoint
- **Staging Fuseki**: 165,619 triples deployed to /koi-staging endpoint
- **All quality gates passing**

## [2.3.0] - 2025-09-28

### Added
- **Content-Based Deduplication** - Web pages are now deduplicated based on content hash to prevent reprocessing unchanged content
- **Event Filtering** - Heartbeats and test data are now filtered at the Event Bridge entry point
- **GitHub Activity Sensor** - Added github_activity sensor to pipeline metadata for comprehensive GitHub tracking
- **URL-Based Versioning** - Web pages tracked by URL with superseded_at timestamps for version control

### Fixed
- **White Text on White Background** - Fixed text visibility issues in Transformation Provenance UI
- **Test Data in Production** - Cleaned up 1,683 test/heartbeat entries from koi_content table
- **UI Flash Issue** - Removed hardcoded examples that briefly appeared before real data loaded
- **Duplicate Processing** - Fixed unnecessary reprocessing of unchanged web page content

### Changed
- **Removed Sensor Status Tab** - Consolidated sensor information into Overview tab
- **Pipeline Metadata API** - Enhanced filtering to exclude test/heartbeat/demo data from RID listings
- **UI Loading States** - Added proper loading spinners instead of placeholder examples

### Cleaned
- **Database Cleanup** - Removed 1,794 heartbeat memories from koi_memories table
- **Test Files** - Removed temporary test scripts from koi-processor directory

## [2.2.0] - 2025-09-25

### Added
- **Source Tracking for Daily Posts** - All daily posts now include source provenance to prevent AI hallucination
- **Podcast Generation** - Full audio podcast generation using OpenAI TTS with automatic chunking for long content
- **Markdown Export for NotebookLM** - Weekly digests can be exported as markdown for NotebookLM integration
- **Audio Player in Dashboard** - Integrated HTML5 audio player with download capabilities
- **Loading Feedback** - Spinner and status messages during podcast generation
- **Nginx Configuration** - Added podcast_audio location block for proper file serving

### Fixed
- **Fake Username Generation** - LLM no longer invents fake usernames like "@EcoWarrior123"
- **502 Bad Gateway Error** - Fixed nginx proxy configuration for public URL access
- **Audio File Serving** - Properly encoded URLs and added Accept-Ranges header for streaming
- **Markdown Download Links** - Fixed base path issues for downloads through public URL

### Changed
- **Podcast Duration** - Expanded from 1 minute to 8+ minutes with comprehensive content
- **Daily Curator Prompts** - Added strict rules to prevent fake data generation
- **Script Generation** - Now uses full digest content including brief_content and podcast_script fields

## [2.1.0] - 2025-09-24

### Added
- **Comprehensive Regen Ledger Integration** - Full on-chain data fetching from all Cosmos SDK modules
- **LLM-Enhanced Content Curation** - AI-powered daily and weekly digest generation
- **On-Chain Activity Tracking** - Weekly digests now include ledger statistics and network metrics
- **Discourse Sensor Integration** - Forum posts properly indexed with published_at dates

### Fixed
- **Daily Generation 502 Error** - Dashboard now properly loads environment variables
- **Weekly Generation Timeout** - Increased timeout to 5 minutes for comprehensive data fetching
- **GitHub URL Malformation** - Cleaned temporary directory paths from sensor URLs
- **Forum Post Dating** - Fixed published_at extraction for all discourse content
- **Post Truncation** - Daily posts now display full content in dashboard

### Changed
- **Daily Curator** - Now uses `daily_curator_llm.py` with OpenAI integration
- **Weekly Curator** - Now uses `weekly_curator_llm.py` with comprehensive ledger data
- **Dashboard Timeout** - Increased subprocess timeout from 180s to 300s

## [2.0.0] - 2025-09-09

### Added
- **RID-based Deduplication System** - Prevents duplicate content ingestion using Resource Identifiers
- **Version Control for Updates** - UPDATE events create new versions with full audit trail
- **Isolated KOI Tables** - New `koi_memories` and `koi_embeddings` tables separate sensor data from scraped content
- **Event Bridge v2** (`koi_event_bridge_v2.py`) - Complete rewrite with deduplication and versioning
- **Database Migration 003** - Creates isolated tables with version tracking
- **Comprehensive Documentation** - Complete README with installation, configuration, and troubleshooting
- **Setup Script** (`scripts/setup.sh`) - Automated environment setup
- **Integration Test Suite** (`scripts/test_pipeline.py`) - End-to-end pipeline testing
- **Environment Configuration** (`.env.example`) - Template for all configuration options
- **BGE-large-en-v1.5 Support** - Production-grade 1024-dimensional embeddings
- **MCP Server Integration** - TypeScript implementation for semantic search
- **CAT Receipt System** - Complete provenance tracking for transformations
- **Permission Filtering** - Agent-specific content access control

### Changed
- **Event Processing Logic** - Now checks for existing RIDs before processing
- **Database Schema** - Added version tracking and superseded_at timestamps
- **Embedding Storage** - Support for multiple embedding dimensions (768, 1024, 1536)
- **Error Handling** - More robust with graceful fallbacks
- **Logging** - Enhanced with color output and structured messages
- **API Responses** - Include version information and previous_version_id

### Fixed
- **Duplicate Content Issue** - RID-based deduplication prevents duplicate ingestion
- **Update Event Handling** - Properly creates new versions instead of duplicating
- **Memory Leaks** - Improved connection pooling and resource cleanup
- **BGE Server Compatibility** - Handles both "text" and "input" field formats
- **Database Connection Issues** - Better error handling and retry logic

### Security
- **Environment Variables** - All sensitive configuration moved to .env file
- **SQL Injection Prevention** - Parameterized queries throughout
- **Input Validation** - Pydantic models for all API inputs

## [1.0.0] - 2025-09-07

### Added
- Initial KOI Event Bridge implementation
- BGE mock server for testing
- Basic PostgreSQL integration
- Simple chunking algorithm
- FastAPI-based REST API
- Agent memory format support

### Known Issues (Fixed in v2)
- No deduplication mechanism
- UPDATE events not properly handled
- Mixed data sources in same tables
- No version tracking

## [0.1.0] - 2025-09-01

### Added
- Project initialization
- Basic project structure
- Research documentation
- Initial prototypes

---

## Migration Guide

### From v1.0.0 to v2.0.0

1. **Database Migration Required**
   ```bash
   psql -U postgres -d eliza < migrations/003_create_isolated_koi_tables.sql
   ```

2. **Environment Variable Changes**
   - Add `USE_ISOLATED_TABLES=true` to your .env file
   - Review new options in .env.example

3. **Code Changes**
   - Replace `koi_event_bridge.py` with `koi_event_bridge_v2.py`
   - Update any custom integrations to handle version fields in responses

4. **Testing**
   - Run `scripts/test_pipeline.py` to verify the upgrade
   - Check deduplication is working with duplicate RID tests

### Rollback Instructions

If you need to rollback to v1.0.0:
1. Set `USE_ISOLATED_TABLES=false` in .env
2. Use `koi_event_bridge.py` instead of v2
3. The isolated tables can remain - they won't be used

---

## Upcoming Features (Planned)

- [ ] Batch processing for bulk imports
- [ ] Automatic sensor health monitoring
- [ ] Content change detection with diff generation
- [ ] Multi-model embedding support
- [ ] GraphQL API for complex queries
- [ ] Distributed processing with message queues
- [ ] Real-time WebSocket event streaming
- [ ] Prometheus metrics and Grafana dashboards
---

## [2.0.0] - 2025-12-20

### Added - Hybrid Graph-Boosted RAG

**Major Feature: Entity-Aware Knowledge Retrieval**

The KOI Query API now implements a sophisticated Hybrid Graph-Boosted RAG system that combines:
- **Vector Search**: Semantic similarity using BGE-1024 embeddings
- **Entity/Graph Search**: Knowledge graph traversal via entity-chunk links
- **Keyword Search**: Full-text search with PostgreSQL tsvector

**Key Components:**

1. **Entity Registry** (koi_entity_registry)
   - Canonical entity storage with type information
   - Semantic deduplication (Tier 1: Exact, Tier 2: Embedding similarity)
   - 12,985 unique entities, 43,430 mentions, 70.10% dedup rate

2. **Entity-Chunk Links** (koi_entity_chunk_links)
   - 614,021 entity-memory associations
   - Enables graph-based document retrieval
   - Source: PostgreSQL entity extraction pipeline

3. **Weighted Average Fusion** (adaptive-features.ts)
   - Formula: 0.6*vector + 0.2*entity + 0.2*keyword + 0.15*entity_boost 
   - Entity boost applied when document appears in both vector and entity results

4. **Source-Diversity Sampling**
   - Prevents high-volume sources (GitHub) from drowning out high-value sources (Homepage)
   - Partitions entity results by source type: web, github, gitlab, other
   - Domain-level diversity for web: regen.network, forum, registry, guides
   - Top 25 from each non-web source, top 10 from each web domain

**Performance:**
- Quality: 99.7% (up from 62%)
- Entity extraction: 3,497 documents processed
- Zero type collisions in entity registry

### Changed
- Entity search now uses source-diversity sampling instead of simple LIMIT
- Fusion algorithm changed from RRF to Weighted Average for better score discrimination
- Added superseded_at IS NULL filter to all search queries

### Technical Details
See docs/HYBRID_RAG_ARCHITECTURE.md for complete technical documentation.
