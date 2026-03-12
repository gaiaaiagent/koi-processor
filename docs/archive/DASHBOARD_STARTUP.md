# KOI Dashboard Startup Guide

## Quick Start

### Start Dashboard
```bash
cd /opt/projects/koi-processor
./start_dashboard.sh
```

### Stop Dashboard
```bash
cd /opt/projects/koi-processor
./stop_dashboard.sh
```

### Check Status
```bash
# Check if running
ps aux | grep content_dashboard

# Check logs
tail -f logs/dashboard.log

# Test endpoint
curl http://localhost:8400/
```

## Access URLs

- **Local**: http://localhost:8400
- **Public**: https://regen.gaiaai.xyz/digests/

## What Was Fixed (2025-10-08)

### Problem: 502 Bad Gateway

The dashboard wasn't running, causing nginx to return 502 errors.

### Root Cause

1. No systemd service configured to auto-start dashboard
2. Dashboard had never been started after server reboot
3. Missing import fix needed for podcast job queue

### Solutions Applied

1. **Fixed import paths** in `content_dashboard.py:31-35`
   - Added fallback import for `podcast_job_queue`
   - Works whether run as module or script

2. **Created startup scripts**
   - `start_dashboard.sh` - Start dashboard with PID tracking
   - `stop_dashboard.sh` - Gracefully stop dashboard

3. **Started the dashboard**
   - Now running on port 8400
   - Proxied via nginx to https://regen.gaiaai.xyz/digests/

## Persistence Options

### Option 1: Manual Start (Current)

Start dashboard manually after each reboot:
```bash
cd /opt/projects/koi-processor
./start_dashboard.sh
```

### Option 2: Systemd Service (Recommended)

Install systemd service for auto-start on boot:

```bash
# Copy service file
sudo cp /tmp/koi-dashboard.service /etc/systemd/system/

# Enable and start
sudo systemctl daemon-reload
sudo systemctl enable koi-dashboard
sudo systemctl start koi-dashboard

# Check status
sudo systemctl status koi-dashboard
```

Service file location: `/tmp/koi-dashboard.service`

### Option 3: Cron Job

Add to user's crontab:
```bash
crontab -e

# Add this line
@reboot cd /opt/projects/koi-processor && ./start_dashboard.sh
```

## Monitoring

### Check Dashboard Health
```bash
# HTTP status
curl -I http://localhost:8400/

# API health
curl http://localhost:8400/api/dashboard/overview
```

### View Logs
```bash
# Real-time logs
tail -f /opt/projects/koi-processor/logs/dashboard.log

# Recent errors
grep -i error /opt/projects/koi-processor/logs/dashboard.log | tail -20
```

### Process Management
```bash
# Find dashboard PID
cat /opt/projects/koi-processor/logs/dashboard.pid

# Check process
ps aux | grep content_dashboard

# Memory usage
ps aux | grep content_dashboard | awk '{print $4, $6}'
```

## Troubleshooting

### Dashboard Won't Start

**Check dependencies:**
```bash
source venv/bin/activate
pip install -r requirements.txt
```

**Check database:**
```bash
psql -h localhost -p 5433 -U postgres -d eliza -c "SELECT 1"
```

**Check port availability:**
```bash
lsof -i :8400
```

### 502 Error Returns

**Verify dashboard is running:**
```bash
curl http://localhost:8400/
```

**If not running, restart:**
```bash
./start_dashboard.sh
```

**Check nginx config:**
```bash
sudo nginx -t
sudo systemctl reload nginx
```

### High Memory Usage

Dashboard may consume more memory over time due to:
- In-memory job queue
- Flask debug mode
- Multiple background threads

**Restart periodically:**
```bash
./stop_dashboard.sh
./start_dashboard.sh
```

## Production Recommendations

1. **Use Gunicorn** instead of Flask dev server:
   ```bash
   gunicorn -w 4 -b 0.0.0.0:8400 src.content.content_dashboard:app
   ```

2. **Setup systemd service** for auto-restart

3. **Monitor with tools like:**
   - Supervisor
   - PM2
   - systemd watchdog

4. **Setup log rotation:**
   ```bash
   # /etc/logrotate.d/koi-dashboard
   /opt/projects/koi-processor/logs/dashboard.log {
       daily
       rotate 7
       compress
       delaycompress
       missingok
       notifempty
   }
   ```

## Related Files

- Dashboard app: `src/content/content_dashboard.py`
- Podcast queue: `src/content/podcast_job_queue.py`
- Nginx config: `/etc/nginx/sites-available/regen-digests.conf`
- Systemd service: `/tmp/koi-dashboard.service`
- Startup script: `start_dashboard.sh`
- Stop script: `stop_dashboard.sh`
- Logs: `logs/dashboard.log`
