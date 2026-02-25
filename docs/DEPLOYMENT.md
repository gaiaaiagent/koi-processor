# KOI Processor Deployment Guide

This guide covers deployment of the KOI Processor to production environments.

## Table of Contents
- [Prerequisites](#prerequisites)
- [Server Requirements](#server-requirements)
- [Production Deployment](#production-deployment)
- [Docker Deployment](#docker-deployment)
- [Monitoring](#monitoring)
- [Backup and Recovery](#backup-and-recovery)
- [Scaling](#scaling)

## Prerequisites

### System Requirements
- Ubuntu 20.04+ or similar Linux distribution
- Python 3.8+ 
- PostgreSQL 14+ with pgvector extension
- 4GB+ RAM (8GB recommended)
- 10GB+ disk space
- Network access to required ports

### Required Ports
- 8090: BGE Embedding Server
- 8100: KOI Event Bridge
- 8200: KOI Coordinator (if running locally)
- 5433: PostgreSQL (or your configured port)

## Production Deployment

## Indexing Hygiene Guardrail

To prevent accidental double-indexing of derived crawl artifacts (e.g., crawl dumps committed to GitHub), run:

```bash
# Uses POSTGRES_URL if set; falls back to local default
python3 scripts/check_indexing_hygiene.py
```

Exit codes:
- `0` = no violations
- `1` = violations found (see printed patterns + sample RIDs)
- `2` = could not run the check (e.g., DB connection failure)

### 1. Server Setup

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install Python and dependencies
sudo apt install python3.8 python3-pip python3-venv postgresql-client git -y

# Install PostgreSQL with pgvector
sudo apt install postgresql-14 postgresql-contrib-14 -y
sudo apt install postgresql-14-pgvector -y

# Create application user
sudo useradd -m -s /bin/bash koi
sudo usermod -aG sudo koi
```

### 2. Application Deployment

```bash
# Switch to koi user
sudo su - koi

# Clone repository
git clone https://github.com/yourusername/koi-processor.git
cd koi-processor

# Setup Python environment
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with production values
nano .env
```

### 3. Database Setup

```bash
# Connect to PostgreSQL
sudo -u postgres psql

# Create database and user
CREATE USER koi WITH PASSWORD 'secure_password';
CREATE DATABASE eliza OWNER koi;
\c eliza
CREATE EXTENSION IF NOT EXISTS vector;
\q

# Run migrations
psql -U koi -d eliza < migrations/001_create_transformation_receipts.sql
psql -U koi -d eliza < migrations/002_create_agent_knowledge_permissions.sql
psql -U koi -d eliza < migrations/003_create_isolated_koi_tables.sql
```

### 4. Systemd Services

Create service files for each component:

#### BGE Server Service
```bash
sudo nano /etc/systemd/system/koi-bge.service
```

```ini
[Unit]
Description=KOI BGE Embedding Server
After=network.target

[Service]
Type=simple
User=koi
WorkingDirectory=/home/koi/koi-processor
Environment="PATH=/home/koi/koi-processor/venv/bin"
ExecStart=/home/koi/koi-processor/venv/bin/python bge_server.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

#### Event Bridge Service
```bash
sudo nano /etc/systemd/system/koi-bridge.service
```

```ini
[Unit]
Description=KOI Event Bridge v2
After=network.target postgresql.service koi-bge.service

[Service]
Type=simple
User=koi
WorkingDirectory=/home/koi/koi-processor
Environment="PATH=/home/koi/koi-processor/venv/bin"
Environment="USE_ISOLATED_TABLES=true"
Environment="PRODUCTION_MODE=true"
ExecStart=/home/koi/koi-processor/venv/bin/python koi_event_bridge_v2.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

#### Enable and Start Services
```bash
sudo systemctl daemon-reload
sudo systemctl enable koi-bge koi-bridge
sudo systemctl start koi-bge koi-bridge

# Check status
sudo systemctl status koi-bge
sudo systemctl status koi-bridge
```

### 5. Nginx Reverse Proxy (Optional)

```bash
sudo apt install nginx -y
sudo nano /etc/nginx/sites-available/koi
```

```nginx
upstream koi_bridge {
    server 127.0.0.1:8100;
}

upstream koi_bge {
    server 127.0.0.1:8090;
}

server {
    listen 80;
    server_name koi.yourdomain.com;

    # Event Bridge
    location /api/koi/ {
        proxy_pass http://koi_bridge/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # BGE Server (internal only)
    location /api/bge/ {
        # Restrict to internal network
        allow 10.0.0.0/8;
        allow 172.16.0.0/12;
        allow 192.168.0.0/16;
        deny all;
        
        proxy_pass http://koi_bge/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/koi /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

## Docker Deployment

### 1. Build Docker Image

Create `Dockerfile`:
```dockerfile
FROM python:3.8-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . .

# Create non-root user
RUN useradd -m -u 1000 koi && chown -R koi:koi /app
USER koi

# Default to Event Bridge
CMD ["python", "koi_event_bridge_v2.py"]
```

### 2. Docker Compose

Create `docker-compose.yml`:
```yaml
version: '3.8'

services:
  postgres:
    image: pgvector/pgvector:pg14
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: eliza
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./migrations:/docker-entrypoint-initdb.d
    ports:
      - "5433:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 10s
      timeout: 5s
      retries: 5

  bge-server:
    build: .
    command: python bge_server.py
    ports:
      - "8090:8090"
    environment:
      - BGE_SERVER_PORT=8090
    restart: unless-stopped

  event-bridge:
    build: .
    command: python koi_event_bridge_v2.py
    ports:
      - "8100:8100"
    environment:
      - POSTGRES_URL=postgresql://postgres:postgres@postgres:5432/eliza
      - BGE_API_URL=http://bge-server:8090/encode
      - USE_ISOLATED_TABLES=true
    depends_on:
      postgres:
        condition: service_healthy
      bge-server:
        condition: service_started
    restart: unless-stopped

volumes:
  postgres_data:
```

### 3. Deploy with Docker

```bash
# Build and start services
docker-compose up -d

# View logs
docker-compose logs -f event-bridge

# Scale if needed
docker-compose up -d --scale event-bridge=3
```

## Monitoring

### 1. Health Checks

```bash
# Create health check script
cat > /home/koi/health_check.sh << 'EOF'
#!/bin/bash

# Check Event Bridge
curl -f http://localhost:8100/ || exit 1

# Check BGE Server
curl -f -X POST http://localhost:8090/encode \
  -H "Content-Type: application/json" \
  -d '{"text":"health check"}' || exit 1

# Check database
psql -U koi -d eliza -c "SELECT 1" || exit 1

echo "All services healthy"
EOF

chmod +x /home/koi/health_check.sh

# Add to cron
crontab -e
# Add: */5 * * * * /home/koi/health_check.sh || systemctl restart koi-bridge
```

### 2. Logging

Configure centralized logging:
```bash
# Install rsyslog
sudo apt install rsyslog -y

# Configure application logging
cat >> /home/koi/koi-processor/.env << EOF
LOG_FILE=/var/log/koi/event-bridge.log
LOG_LEVEL=INFO
EOF

# Create log directory
sudo mkdir -p /var/log/koi
sudo chown koi:koi /var/log/koi

# Setup log rotation
sudo nano /etc/logrotate.d/koi
```

```
/var/log/koi/*.log {
    daily
    rotate 30
    compress
    delaycompress
    notifempty
    create 640 koi koi
    sharedscripts
    postrotate
        systemctl reload koi-bridge
    endscript
}
```


### 3. Event Flood Detection

Monitors for abnormally high event rates that could indicate a sensor malfunction or external attack.

**Systemd Service:** `koi-event-flood-monitor.service`  
**Timer:** `koi-event-flood-monitor.timer` (runs every 5 minutes)

**Thresholds:**
- Normal: < 100 events/5min
- Warning: 100-500 events/5min (logged)
- Critical: > 500 events/5min (email alert)

```bash
# Check flood monitor status
sudo systemctl status koi-event-flood-monitor.timer

# View flood detection logs
tail -f /opt/projects/koi-processor/logs/event_flood.log

# Manually run flood check
sudo /opt/projects/koi-processor/monitoring/event_flood_monitor.sh
```

**Alert Configuration:**

Email alerts require:
1. `msmtp` installed and configured (`~/.msmtprc`)
2. `ALERT_EMAIL` set in `/opt/projects/koi-processor/.alert-config`

Example `.alert-config`:
```bash
ALERT_EMAIL=your-email@example.com
```

**Context:** Created after the Jan 9-10, 2026 flood incident where 57,116 false CONTENT_CHANGED events were generated in 24 hours.

### 4. Metrics (Prometheus)

Add Prometheus metrics endpoint:
```python
# In koi_event_bridge_v2.py
from prometheus_client import Counter, Histogram, generate_latest

# Metrics
events_processed = Counter('koi_events_processed', 'Total events processed')
processing_time = Histogram('koi_processing_seconds', 'Event processing time')

@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type="text/plain")
```

## Backup and Recovery

### 1. Database Backup

```bash
# Create backup script
cat > /home/koi/backup.sh << 'EOF'
#!/bin/bash
BACKUP_DIR="/home/koi/backups"
DATE=$(date +%Y%m%d_%H%M%S)
mkdir -p $BACKUP_DIR

# Backup database
pg_dump -U koi -d eliza | gzip > $BACKUP_DIR/eliza_$DATE.sql.gz

# Keep only last 7 days
find $BACKUP_DIR -name "*.sql.gz" -mtime +7 -delete

echo "Backup completed: $BACKUP_DIR/eliza_$DATE.sql.gz"
EOF

chmod +x /home/koi/backup.sh

# Add to cron (daily at 2 AM)
crontab -e
# Add: 0 2 * * * /home/koi/backup.sh
```

### 2. Restore Procedure

```bash
# Stop services
sudo systemctl stop koi-bridge koi-bge

# Restore database
gunzip < /home/koi/backups/eliza_20250909_020000.sql.gz | psql -U koi -d eliza

# Start services
sudo systemctl start koi-bge koi-bridge
```

## Scaling

### Horizontal Scaling

1. **Multiple Event Bridge Instances**
   - Deploy multiple Event Bridge instances
   - Use a load balancer (nginx, HAProxy)
   - Ensure `USE_ISOLATED_TABLES=true` on all instances

2. **Database Scaling**
   - Use PostgreSQL replication for read replicas
   - Consider partitioning large tables by date
   - Use connection pooling (pgBouncer)

3. **BGE Server Scaling**
   - Deploy multiple BGE servers
   - Use round-robin DNS or load balancer
   - Consider GPU instances for real BGE model

### Vertical Scaling

1. **Database Optimization**
   ```sql
   -- Increase shared buffers
   ALTER SYSTEM SET shared_buffers = '2GB';
   
   -- Optimize for SSD
   ALTER SYSTEM SET random_page_cost = 1.1;
   
   -- Increase work memory
   ALTER SYSTEM SET work_mem = '256MB';
   
   -- Reload configuration
   SELECT pg_reload_conf();
   ```

2. **Application Tuning**
   - Increase worker processes in uvicorn
   - Adjust chunk size and overlap for your content
   - Implement caching for frequently accessed data

## Troubleshooting

### Common Issues

1. **High Memory Usage**
   - Reduce chunk size
   - Implement connection pooling
   - Add memory limits to systemd services

2. **Slow Processing**
   - Check database indexes
   - Monitor BGE server response times
   - Consider batching small events

3. **Connection Errors**
   - Verify firewall rules
   - Check PostgreSQL connection limits
   - Review nginx proxy configuration

### Debug Mode

Enable debug logging for troubleshooting:
```bash
# Temporary debug mode
LOG_LEVEL=DEBUG systemctl restart koi-bridge

# View detailed logs
journalctl -u koi-bridge -f
```

---

For additional support, see the main [README.md](README.md) or open an issue on GitHub.
