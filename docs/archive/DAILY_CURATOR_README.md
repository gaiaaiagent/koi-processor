# Daily Content Curator for Regen Network

## Overview

The Daily Content Curator is a specialized processor component within the KOI infrastructure that aggregates and curates content for daily X/Twitter posts and weekly digests. It implements intelligent publication date tracking to ensure only genuinely recent content is selected for daily updates.

## Key Features

### 🗓️ Publication Date Intelligence
- **Distinguishes** between when content was published vs when it was ingested
- **Extracts dates** from multiple sources (meta tags, JSON-LD, URLs, text patterns)
- **Confidence scoring** for extracted dates (0.0 to 1.0)
- **Content deduplication** using SHA-256 hashing

### 📊 Content Aggregation
- Queries recent content from PostgreSQL with pgvector
- Identifies trending topics using BGE embeddings
- Aggregates real-time stats from ledger sensor
- Prioritizes content by relevance and recency

### 🐦 Daily Thread Generation
- Creates 3-5 post threads for X/Twitter
- Includes headline, stats, links, and call-to-action
- Follows Regen Network style guide
- Draft-only mode for first week

### 📚 Weekly Digest Creation
- 800-1200 word briefs with citations
- Structured sections for NotebookLM ingestion
- Markdown export format
- Comprehensive metadata tracking

## Architecture

```
KOI Sensors → Event Bridge → PostgreSQL → Daily Curator → X Bot / Weekly Digest
                     ↓
              Publication Date Extraction
                     ↓
              Content Filtering by Date
```

## Installation

### Prerequisites
- Python 3.8+
- PostgreSQL with pgvector extension
- Running KOI infrastructure
- BGE embedding server (optional)

### Setup

1. Install dependencies:
```bash
cd /Users/darrenzal/projects/RegenAI/koi-processor
pip install -r requirements.txt
```

2. Run database migration:
```bash
python scripts/run_daily_curator.py migrate
```

3. Configure settings:
```bash
# Edit config/curator_config.yaml
# Set database URL, BGE server URL, etc.
```

## Usage

### Command Line Interface

```bash
# Check content status
python scripts/run_daily_curator.py status

# Generate daily thread
python scripts/run_daily_curator.py daily

# Generate daily thread and save to file
python scripts/run_daily_curator.py daily -o output/thread_2025-09-11.json

# Generate weekly digest
python scripts/run_daily_curator.py weekly -o output/digest_week_37.json

# Enable verbose logging
python scripts/run_daily_curator.py daily -v
```

### Programmatic Usage

```python
from daily_curator import DailyCurator

# Initialize curator
curator = DailyCurator(config_path='config/curator_config.yaml')

# Generate daily thread
thread = await curator.generate_daily_thread()

# Generate weekly digest
digest = await curator.generate_weekly_digest()

# Export for NotebookLM
markdown = await curator.export_for_notebooklm(digest)
```

## Publication Date Extraction

The system extracts publication dates from various sources:

### High Confidence (0.9-1.0)
- RSS `pubDate` fields
- API `created_at` timestamps
- Meta tags: `article:published_time`, `datePublished`
- JSON-LD structured data

### Medium Confidence (0.6-0.8)
- URL date patterns (`/2025/09/11/`)
- HTML `<time>` elements
- Modified dates as fallback

### Low Confidence (0.3-0.5)
- Text pattern matching
- Contextual date extraction

## Content Selection Algorithm

1. **Primary Window** (24-48 hours)
   - Content actually published recently
   - High confidence dates required (≥0.7)

2. **Secondary Window** (up to 1 week)
   - Important updates regardless of date
   - Medium confidence acceptable (≥0.5)

3. **Evergreen Fallback**
   - Mix recent + timeless content
   - Used when insufficient daily content

## Database Schema

The migration adds these fields to `koi_memories`:

```sql
published_at TIMESTAMP WITH TIME ZONE    -- Original publication date
published_confidence FLOAT               -- Confidence score (0-1)
content_hash VARCHAR(64)                 -- SHA-256 for deduplication
last_seen_at TIMESTAMP WITH TIME ZONE    -- Last observation time
```

## Configuration

Key settings in `config/curator_config.yaml`:

```yaml
# Time windows for content selection
hours_lookback_primary: 48      # New content window
hours_lookback_secondary: 168   # Fallback window (1 week)

# Quality thresholds
min_publication_confidence: 0.5  # Minimum date confidence

# Thread settings
max_thread_posts: 5              # Maximum posts per thread
min_thread_posts: 3              # Minimum posts per thread

# Source priorities (higher = more important)
source_priorities:
  ledger: 10
  governance: 9
  discourse: 7
  twitter: 6
  medium: 6
```

## Output Format

### Daily Thread JSON
```json
{
  "thread_date": "2025-09-11T12:00:00Z",
  "posts": [
    {
      "type": "headline",
      "content": "🌱 Regen Network Daily Update",
      "metadata": {"priority": "high", "position": 1}
    },
    {
      "type": "stat",
      "content": "📊 24h: 5 new credit batches, $2.3M volume",
      "source": "ledger_sensor",
      "published_at": "2025-09-11T10:00:00Z"
    },
    {
      "type": "link",
      "content": "New governance proposal for...",
      "url": "https://forum.regen.network/...",
      "published_at": "2025-09-11T08:30:00Z"
    }
  ],
  "metadata": {
    "content_sources": {
      "new_today": 12,
      "recent_48h": 25,
      "trending_topics": 3
    }
  }
}
```

### Weekly Digest Markdown
```markdown
# Regen Network Weekly Digest
Week ending: 2025-09-11

## Executive Summary
This week saw 47 significant updates across the ecosystem...

## Key Developments
• New credit methodology approved (via discourse)
• Governance proposal #123 passed (via ledger)

## Marketplace Activity
Total volume: $5,234,567
New credit batches: 12
```

## Monitoring & Troubleshooting

### Check Database Connection
```bash
python scripts/run_daily_curator.py status
```

### Common Issues

**No recent content found:**
- Check if sensors are running and publishing events
- Verify publication date extraction is working
- Review confidence thresholds in config

**Database migration fails:**
- Ensure PostgreSQL is running
- Check user has CREATE/ALTER permissions
- Verify pgvector extension is installed

**BGE server unavailable:**
- Trending topics will be limited
- System continues without embeddings
- Check BGE server is running on configured port

## Integration with X Bot

The Daily Curator outputs JSON that can be consumed by the X bot:

```bash
# Generate thread
python scripts/run_daily_curator.py daily -o thread.json

# X bot consumes the output
python ../koi-sensors/bots/x_daily_bot.py --input thread.json
```

## Development Roadmap

### Completed ✅
- Publication date extraction system
- Database schema with date tracking
- Content deduplication
- Daily thread generation
- Weekly digest creation
- CLI interface

### Upcoming
- [ ] Integration with X posting bot
- [ ] NotebookLM API integration
- [ ] Automated scheduling
- [ ] Quality control review system
- [ ] Auto-publish after week 1

## Architecture Decision

The Daily Curator is implemented as a **processor component**, not a KOI node, because:
- KOI nodes are data sources that emit events
- The curator consumes and processes existing data
- Maintains clean separation of concerns
- Follows established KOI architecture patterns

## Contributing

When adding new date extraction patterns:
1. Add to `utils/date_extractor.py`
2. Include confidence scoring logic
3. Test with sample content
4. Document in extraction strategies

## License

Part of the Regen Network KOI infrastructure project.