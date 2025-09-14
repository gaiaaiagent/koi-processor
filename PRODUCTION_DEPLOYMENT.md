# Milestone B Production Deployment Guide

## Session 18: Production Deployment

This guide is for deploying the Milestone B KOI Processor system to production server.

## Prerequisites

Before deployment, ensure:
- PostgreSQL is running on port 5433 with database `eliza`
- Python 3.11+ is installed
- Git access to the repository
- Sufficient disk space (at least 5GB)

## Deployment Steps

### 1. Clone or Update Repository

```bash
# If first time deployment
cd /opt/projects
git clone https://github.com/yourusername/koi-processor.git
cd koi-processor

# If updating existing deployment
cd /opt/projects/koi-processor
git pull origin main
```

### 2. Run Setup Script

The setup script handles all initialization:

```bash
# Run the comprehensive setup
bash scripts/setup.sh
```

This will:
- Create virtual environment
- Install all Python dependencies
- Set up database tables
- Create configuration files
- Initialize directories

**For database migrations with backup (recommended):**
```bash
# Use this instead of regular migrations for safety
bash scripts/run_migrations_with_backup.sh
```
This will:
- Create compressed backup before migrations
- Apply all migrations
- Keep last 5 backups automatically
- Provide restore instructions if anything fails

### 3. Configure Environment

Create or update `.env` file with production values:

```bash
# Copy example and edit
cp .env.example .env
nano .env
```

Required configurations:
```env
# Database
POSTGRES_URL=postgresql://postgres:postgres@localhost:5433/eliza

# API Keys (get from your accounts)
OPENAI_API_KEY=your-openai-key
ANTHROPIC_API_KEY=your-anthropic-key  # Optional

# Services
BGE_API_URL=http://localhost:8090/encode
KOI_EVENT_BRIDGE_URL=http://localhost:8100
MCP_SERVER_URL=http://localhost:8200

# Scheduling
DAILY_TRIGGER_TIME=12:00
DAILY_TRIGGER_TZ=America/New_York
WEEKLY_TRIGGER_DAY=Friday
```

### 4. Start All Services

```bash
# Start all Milestone B services
bash scripts/start_all_services.sh
```

This starts:
- BGE Embedding Server (port 8090)
- KOI Event Bridge v2 (port 8100)
- MCP Knowledge Server (port 8200)
- Content Dashboard (port 8400)

### 5. Configure Web Access (Optional)

To set up HTTPS access at a custom domain (e.g., https://regen.gaiaai.xyz/digests):

```bash
# Run the nginx setup script
sudo bash /opt/projects/koi-processor/setup_nginx_digests.sh
```

This will:
- Install nginx (if needed)
- Configure SSL with Let's Encrypt
- Set up proxy from https://regen.gaiaai.xyz/digests to localhost:8400
- Enable WebSocket support for real-time updates

The configuration files are provided:
- `nginx_config_digests.conf` - Nginx configuration template
- `setup_nginx_digests.sh` - Automated setup script

### 6. Verify Deployment

```bash
# Run validation
source venv/bin/activate
python src/utils/complete_validation.py

# Monitor services
bash scripts/monitor_services.sh
```

### 6. Set Up Cron Jobs

Add to crontab for automated scheduling:

```bash
# Edit crontab
crontab -e

# Add these lines:
# Daily Curator - 12:00 ET weekdays
0 12 * * 1-5 cd /opt/projects/koi-processor && source venv/bin/activate && python scripts/run_daily_curator.py >> logs/daily_curator.log 2>&1

# Weekly Aggregator - Friday 2PM
0 14 * * 5 cd /opt/projects/koi-processor && source venv/bin/activate && python scripts/run_weekly_aggregator.py >> logs/weekly_aggregator.log 2>&1
```

### 7. Optional: SystemD Services (Linux)

For automatic startup on boot:

```bash
# Copy service files
sudo cp systemd/*.service /etc/systemd/system/

# Enable and start services
sudo systemctl daemon-reload
sudo systemctl enable koi-bge-server koi-event-bridge koi-mcp-server koi-dashboard
sudo systemctl start koi-bge-server koi-event-bridge koi-mcp-server koi-dashboard
```

## Service Endpoints

After deployment, services will be available at:

- **Content Dashboard**: http://localhost:8400
- **BGE Embeddings API**: http://localhost:8090
- **KOI Event Bridge**: http://localhost:8100
- **MCP Knowledge Server**: http://localhost:8200

## Monitoring

### Check Service Status
```bash
bash scripts/monitor_services.sh
```

### View Logs
```bash
# All logs
tail -f logs/*.log

# Specific service
tail -f logs/event_bridge.log
tail -f logs/dashboard.log
```

### Test Integration
```bash
source venv/bin/activate
python tests/test_integration.py
```

## Troubleshooting

### Service Won't Start
1. Check port availability: `lsof -i :8090`
2. Check Python dependencies: `pip list`
3. Check logs: `tail -100 logs/[service].log`

### Database Connection Issues
1. Verify PostgreSQL is running: `psql -h localhost -p 5433 -U postgres -d eliza -c '\l'`
2. Check connection string in `.env`
3. Run migrations: `bash scripts/run_migrations.sh`

### Missing Dependencies
```bash
source venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-audio.txt  # If audio features needed
pip install -r requirements-weekly.txt  # If weekly aggregator needed
```

## Maintenance

### Restart Services
```bash
# Stop all
pkill -f "bge_server|event_bridge|mcp_server|dashboard"

# Start all
bash scripts/start_all_services.sh
```

### Update Code
```bash
git pull origin main
source venv/bin/activate
pip install -r requirements.txt
bash scripts/run_migrations.sh
bash scripts/start_all_services.sh
```

### Backup Database
```bash
pg_dump -h localhost -p 5433 -U postgres eliza > backup_$(date +%Y%m%d).sql
```

## Production Checklist

- [ ] PostgreSQL running and accessible
- [ ] Python virtual environment created
- [ ] All dependencies installed
- [ ] `.env` file configured with production values
- [ ] Database migrations applied
- [ ] All services started and responding
- [ ] Cron jobs configured
- [ ] Monitoring dashboard accessible
- [ ] Validation tests passing
- [ ] Logs being generated
- [ ] Backup strategy in place

## Support

For issues or questions:
- Check logs in `/opt/projects/koi-processor/logs/`
- Run validation: `python src/utils/complete_validation.py`
- Review documentation in `/opt/projects/koi-processor/docs/`

---

## Quick Command for Claude Code on Server

Copy and paste this complete deployment command:

```bash
# Complete deployment in one command
cd /opt/projects && \
git clone https://github.com/yourusername/koi-processor.git && \
cd koi-processor && \
bash scripts/setup.sh && \
cp .env.example .env && \
echo "Please edit .env with your API keys and configuration" && \
nano .env && \
bash scripts/start_all_services.sh && \
bash scripts/monitor_services.sh
```

After running, verify at: http://localhost:8400