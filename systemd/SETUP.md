# Systemd Services Setup

## Overview
This guide helps you set up KOI services as systemd services for production stability.

## Current Status
As of now, the services are running as regular processes:
- BGE Server: Running on port 8090 ✅
- Event Bridge v2: Running on port 8100 ✅
- PostgreSQL: Running on port 5433 ✅

## Setup Instructions

### 1. Copy Service Files
```bash
# Copy service files to systemd directory
sudo cp systemd/koi-bge.service /etc/systemd/system/
sudo cp systemd/koi-bridge.service /etc/systemd/system/
```

### 2. Create Log Directory
```bash
# Create log directory
sudo mkdir -p /var/log/koi
sudo chown darren:darren /var/log/koi
```

### 3. Stop Current Processes
```bash
# Find and stop current processes
pkill -f bge_server.py
pkill -f koi_event_bridge_v2.py
```

### 4. Enable and Start Services
```bash
# Reload systemd
sudo systemctl daemon-reload

# Enable services to start on boot
sudo systemctl enable koi-bge koi-bridge

# Start services
sudo systemctl start koi-bge
sudo systemctl start koi-bridge
```

### 5. Verify Services
```bash
# Check status
sudo systemctl status koi-bge
sudo systemctl status koi-bridge

# Check logs
sudo journalctl -u koi-bge -f
sudo journalctl -u koi-bridge -f
```

## Monitoring

### Manual Health Check
```bash
# Run monitoring script
bash scripts/monitor_services.sh
```

### Automated Monitoring (Cron)
```bash
# Add to crontab
crontab -e

# Add this line to check every 5 minutes
*/5 * * * * /opt/projects/koi-processor/scripts/monitor_services.sh >> /var/log/koi/monitor.log 2>&1
```

### Service Management Commands
```bash
# Start services
sudo systemctl start koi-bge koi-bridge

# Stop services
sudo systemctl stop koi-bge koi-bridge

# Restart services
sudo systemctl restart koi-bge koi-bridge

# View logs
sudo journalctl -u koi-bge --since "1 hour ago"
sudo journalctl -u koi-bridge --since "1 hour ago"

# Follow logs in real-time
sudo journalctl -u koi-bge -f
sudo journalctl -u koi-bridge -f
```

## Troubleshooting

### If services won't start:
1. Check log files:
   ```bash
   sudo journalctl -u koi-bge -n 50
   sudo journalctl -u koi-bridge -n 50
   ```

2. Verify Python environment:
   ```bash
   /opt/projects/koi-processor/venv/bin/python --version
   ```

3. Check port availability:
   ```bash
   sudo lsof -i :8090
   sudo lsof -i :8100
   ```

4. Test manually:
   ```bash
   cd /opt/projects/koi-processor
   source venv/bin/activate
   python bge_server.py
   # In another terminal:
   python koi_event_bridge_v2.py
   ```

## Benefits of Systemd Services
- ✅ Automatic restart on failure
- ✅ Start on system boot
- ✅ Centralized logging with journald
- ✅ Resource limiting capabilities
- ✅ Dependency management
- ✅ Clean shutdown handling

## Current Production Status
The services are currently running and healthy:
- All endpoints responding correctly
- Deduplication working
- Database connections stable
- No immediate action required

However, setting up systemd services is recommended for production stability.