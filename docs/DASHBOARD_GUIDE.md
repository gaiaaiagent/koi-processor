# Milestone B Content Operations Dashboard

## 🎯 Overview

The Content Operations Dashboard provides real-time monitoring and control for the Milestone B Daily Bot and Weekly Digest systems. It offers a web-based interface for reviewing content, tracking quality metrics, and managing the publication pipeline.

### Current Status (September 2025)
- **553 documents** processed in last 24 hours
- **17,612 BGE embeddings** generated
- End-to-end latency: **3-5 seconds**
- Active sensors: Discourse Forum, Website, GitHub, Twitter, Medium

## Features

### 🎯 Core Capabilities

- **Real-time Monitoring**: Live updates via WebSocket connections
- **Content Review**: Interactive approval workflow for drafts
- **Quality Metrics**: Style scores, validation results, and compliance tracking
- **Schedule Management**: View and trigger daily/weekly content generation
- **Alert System**: Error tracking and notification management
- **Performance Analytics**: Historical trends and success metrics

### 📊 Dashboard Sections

1. **Overview Panel**: System health, pending reviews, KOI pipeline status
2. **Daily Bot Monitor**: Today's draft, performance charts, source tracking
3. **Weekly Digest Monitor**: Progress tracking, word count, podcast status
4. **Quality Control**: Pending reviews, approval statistics, style scores
5. **Schedule**: Upcoming runs, manual triggers, publishing calendar
6. **Alerts**: Error logs, system issues, resolution tracking

## Installation

### Prerequisites

- Python 3.8+
- PostgreSQL with the Milestone B database schema
- KOI pipeline components running (Event Bridge, BGE Server, etc.)

### Quick Setup (Recommended)

Use the automated setup script:
```bash
cd /opt/projects/koi-processor
./scripts/setup_dashboard.sh
```

This script will:
- Check Python version
- Create/activate virtual environment
- Install dependencies
- Run database migrations
- Set up configuration
- Create required directories

### Manual Setup Steps

1. **Create and Activate Virtual Environment**
```bash
cd /opt/projects/koi-processor
python3 -m venv venv
source venv/bin/activate
```

2. **Install Dependencies**
```bash
pip install -r requirements.txt
```

3. **Run Database Migrations**
```bash
# Run all migrations including dashboard tables
./scripts/run_migrations.sh

# Or run just the dashboard migration
psql postgresql://postgres:postgres@localhost:5433/eliza -f migrations/005_create_dashboard_tables.sql
```

4. **Configure Dashboard (Optional)**
The default configuration works out of the box. To customize, edit `config/dashboard_config.yaml`:
```yaml
dashboard:
  port: 8400
  auth_enabled: false  # Set to true in production

database:
  host: localhost
  port: 5433
  name: eliza
```

5. **Start the Dashboard**
```bash
source venv/bin/activate  # If not already activated
python content_dashboard.py
```

The dashboard will be available at `http://localhost:8400`

## 🚀 Deployment Commands

### Generate Daily Thread
```bash
cd /opt/projects/koi-processor
source venv/bin/activate
set -a && source .env && set +a
python scripts/run_daily_curator.py daily -o output/daily_poc.json
```

### Generate Weekly Digest (LLM-Powered)
```bash
# New LLM-powered curator that includes ALL content
python scripts/run_weekly_curator_llm.py

# Legacy aggregator (if needed)
python scripts/run_weekly_aggregator.py \
  --output-dir output \
  --format both
```

### Access Points
- **Content Dashboard**: https://regen.gaiaai.xyz:8400/
- **Digests Page**: https://regen.gaiaai.xyz/digests/
- **KOI Status**: https://regen.gaiaai.xyz/koi/
- **Podcastify Integration**: Available at digests page for audio conversion

## 📊 Key Metrics

| Metric | Value |
|--------|-------|
| Documents/24h | 553+ |
| BGE Embeddings | 17,612+ |
| Processing Latency | 3-5 sec |
| Daily Posts Generated | 3-5 |
| Weekly Word Count | 800-1200 |
| Active Sources | 5+ |

## 🔄 Complete Data Flow

```
Real Sensors → KOI Coordinator (8005) → Event Bridge v2 (8100)
     ↓
BGE Embeddings (8090) → PostgreSQL (5433)
     ↓
Daily Curator → 5-Post Thread
Weekly Curator (LLM) → Weekly Digest → Podcastify/NotebookLM
```

## Usage

### Accessing the Dashboard

1. Open your browser to `http://localhost:8400`
2. If authentication is enabled, login with your credentials
3. The overview page will load automatically

### Daily Bot Monitoring

1. Navigate to the **Daily Bot** tab
2. Review today's draft thread:
   - Check the style score (should be > 70%)
   - Verify all sources are included
   - Review the thread structure (3-5 posts)
3. Use the action buttons to:
   - **Approve**: Send to publication queue
   - **Request Revision**: Flag for editing
   - **Reject**: Prevent publication

### Weekly Digest Review

1. Navigate to the **Weekly Digest** tab
2. Monitor progress throughout the week:
   - Content collection progress bar
   - Word count tracking (800-1200 words)
   - Source diversity metrics
3. Review the digest brief when ready
4. Check podcast generation status

### Quality Control

1. Navigate to the **Quality Control** tab
2. View pending reviews in the queue
3. Check quality metrics:
   - Approval rate percentage
   - Average style scores
   - Distribution chart (approved/rejected/pending)
4. Click "Review" to examine specific content

### Managing Alerts

1. Navigate to the **Alerts** tab
2. View recent errors and warnings
3. Click "Resolve" to mark issues as handled
4. Use "Clear Resolved" to clean up old alerts

### Manual Triggers

1. Navigate to the **Schedule** tab
2. View upcoming automated runs
3. Use "Run Now" buttons to trigger:
   - Daily bot generation
   - Weekly digest compilation
4. Confirm the action when prompted

## API Integration

### Notifying the Dashboard

Other components can send updates to the dashboard:

```python
import requests

# Send a notification
requests.post('http://localhost:8400/api/dashboard/notify', json={
    'type': 'daily_draft',
    'content': {
        'status': 'generated',
        'style_score': 0.85,
        'posts_count': 4
    }
})

# Send an error alert
requests.post('http://localhost:8400/api/dashboard/notify', json={
    'type': 'error',
    'content': {
        'alert_type': 'sensor_failure',
        'severity': 'warning',
        'message': 'Twitter sensor failed to connect'
    }
})
```

### Available API Endpoints

- `GET /api/dashboard/overview` - System health and overview metrics
- `GET /api/dashboard/daily/stats` - Daily bot statistics
- `GET /api/dashboard/daily/drafts` - Current draft threads
- `GET /api/dashboard/weekly/stats` - Weekly digest statistics
- `GET /api/dashboard/quality/pending` - Content awaiting review
- `GET /api/dashboard/quality/history` - Approval/rejection history
- `GET /api/dashboard/podcast/status` - Podcast generation status
- `GET /api/dashboard/schedule` - Upcoming scheduled runs
- `GET /api/dashboard/errors` - Recent errors and alerts
- `POST /api/dashboard/notify` - Receive notifications from components

## Configuration

### Key Configuration Options

**Quality Thresholds** (`config/dashboard_config.yaml`):
```yaml
thresholds:
  daily_bot:
    min_sources: 3
    style_score_warning: 0.7
    style_score_critical: 0.5
    
  weekly_digest:
    min_word_count: 800
    max_word_count: 1200
    min_sources: 10
```

**Alert Settings**:
```yaml
alerts:
  email_enabled: false
  slack_enabled: false
  severity_levels:
    critical: ["database_connection_failed"]
    warning: ["style_score_low"]
    info: ["draft_generated"]
```

**Schedule Configuration**:
```yaml
schedule:
  daily_bot:
    hour: 16  # 16:00 UTC = 12:00 ET
    days: [0, 1, 2, 3, 4]  # Monday-Friday
    
  weekly_digest:
    day: 4  # Friday
    hour: 16
```

## WebSocket Events

The dashboard uses WebSocket for real-time updates:

### Client Events
- `connect` - Establish connection
- `disconnect` - Close connection
- `request_update` - Request specific data refresh

### Server Events
- `connected` - Connection confirmed
- `dashboard_update` - Data update notification
  - Types: `daily_draft`, `weekly_progress`, `quality_update`, `error`

## Troubleshooting

### Dashboard Won't Start

1. Check PostgreSQL is running:
   ```bash
   docker ps | grep postgres  # If using Docker
   psql postgresql://postgres:postgres@localhost:5433/eliza -c "SELECT 1"
   ```
2. Verify virtual environment is activated:
   ```bash
   which python  # Should show path with 'venv' in it
   ```
3. Ensure port 8400 is not in use:
   ```bash
   lsof -i :8400  # macOS/Linux
   # If something is using it:
   pkill -f content_dashboard.py
   ```
4. Check all dependencies are installed:
   ```bash
   pip list | grep -E "flask|psycopg2|loguru"

### No Data Showing

1. Verify database tables exist:
   ```bash
   psql postgresql://postgres:postgres@localhost:5433/eliza -c "\dt *dashboard*"
   psql postgresql://postgres:postgres@localhost:5433/eliza -c "\dt *content*"
   ```
2. Check if migrations were run:
   ```bash
   psql postgresql://postgres:postgres@localhost:5433/eliza -c "SELECT * FROM schema_migrations WHERE version LIKE '%005%'"
   ```
3. Verify KOI pipeline components (if integrated):
   - Event Bridge (port 8100): `curl http://localhost:8100/health`
   - BGE Server (port 8090): `curl http://localhost:8090/health`
4. Add sample data for testing:
   ```bash
   python scripts/add_sample_dashboard_data.py
   ```

### WebSocket Connection Failed

1. Check browser console for errors
2. Verify Flask-SocketIO is installed
3. Check firewall settings for port 8400
4. Try refreshing the page

### Authentication Issues

1. Verify `auth_enabled` setting in config
2. Default credentials: admin/regen2025
3. Check session timeout settings
4. Clear browser cookies and retry

## Security Considerations

### Production Deployment

1. **Enable Authentication**:
   ```yaml
   dashboard:
     auth_enabled: true
   ```

2. **Change Default Credentials**:
   - Set environment variables:
     ```bash
     export DASHBOARD_USER="your-admin-user"
     export DASHBOARD_PASSWORD="your-secure-password"
     ```
   - Or update in `config/dashboard_config.yaml`:
     ```yaml
     auth:
       username: "your-admin-user"
       password_hash: "bcrypt-hashed-password"
     ```

3. **Use HTTPS**:
   - Deploy behind a reverse proxy (nginx)
   - Configure SSL certificates

4. **Set Strong Secret Key**:
   ```bash
   export FLASK_SECRET_KEY="your-strong-random-secret-key"
   ```

5. **Configure CORS**:
   ```yaml
   security:
     cors_origins: ["https://your-domain.com"]
   ```

## Integration with Existing Components

### Daily Curator Integration

Add to `daily_curator.py`:
```python
def notify_dashboard(update_type, data):
    """Send update to dashboard"""
    try:
        requests.post('http://localhost:8400/api/dashboard/notify', json={
            'type': update_type,
            'content': data
        })
    except Exception as e:
        logger.warning(f"Failed to notify dashboard: {e}")

# After generating draft
notify_dashboard('daily_draft', {
    'status': 'generated',
    'posts_count': len(thread['posts']),
    'style_score': quality_score
})
```

### Weekly Aggregator Integration

Add to `weekly_aggregator.py`:
```python
# Update progress
notify_dashboard('weekly_progress', {
    'progress_percentage': (word_count / 1000) * 100,
    'word_count': word_count,
    'source_count': len(sources)
})
```

### Quality Control Integration

Add to `quality_control.py`:
```python
# After validation
notify_dashboard('quality_update', {
    'content_id': content_id,
    'validation_passed': validation_results['passed'],
    'style_score': style_score
})
```

## Maintenance

### Database Cleanup

Remove old alerts and reviews periodically:
```bash
# Run cleanup script
python scripts/cleanup_dashboard_data.py --days 30

# Or manually via SQL
psql postgresql://postgres:postgres@localhost:5433/eliza << EOF
-- Clean up resolved alerts older than 30 days
DELETE FROM dashboard_alerts 
WHERE resolved = true 
AND created_at < NOW() - INTERVAL '30 days';

-- Archive old content reviews
DELETE FROM content_reviews 
WHERE status IN ('published', 'rejected')
AND created_at < NOW() - INTERVAL '90 days';

-- Clean up old metrics
DELETE FROM dashboard_metrics
WHERE timestamp < NOW() - INTERVAL '30 days';
EOF
```

### Log Rotation

Configure log rotation in `dashboard_config.yaml`:
```yaml
logging:
  file:
    max_size_mb: 10
    backup_count: 5
```

### Performance Optimization

1. **Enable caching** for frequently accessed data
2. **Index database tables** for common queries
3. **Limit historical data** shown in charts
4. **Use pagination** for long lists

## Support

For issues or questions:
1. Check the logs at `logs/dashboard.log`
2. Review the troubleshooting section
3. Contact the development team

## ✅ Milestone B Requirements Met

### Daily Bot "Regen Daily"
- ✅ Generates 3-5 post threads
- ✅ Includes stats, links, CTAs
- ✅ Style guide compliant
- ✅ Draft-mode ready

### Weekly Digest "Regen Weekly"
- ✅ 800-1200 word briefs
- ✅ NotebookLM export format
- ✅ 20-minute podcast capability via Podcastify
- ✅ Citations and references included
- ✅ LLM-powered curation using `run_weekly_curator_llm.py`

### Data Sources
- ✅ Forum discussions (Discourse)
- ✅ Ledger activity (Direct RPC)
- ✅ GitHub repositories
- ✅ Governance notes (regentokenomics.org)
- ✅ Twitter integration
- ✅ Medium blog posts

## Version History

- **v2.0.0** (2025-09-24): Production deployment with LLM curation
  - LLM-powered weekly digest with comprehensive content inclusion
  - Direct ledger access via RPC
  - Podcastify audio generation integration
  - Enhanced sensor reliability and date extraction

- **v1.0.0** (2025-09-14): Initial release with core monitoring features
  - Real-time dashboard with WebSocket support
  - Daily bot and weekly digest monitoring
  - Quality control interface
  - Alert system
  - Schedule management

---
*Generated by KOI Content Pipeline v2.0*