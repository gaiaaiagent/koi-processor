# KOI Pipeline Rollback Procedures

## Overview
This document provides step-by-step procedures to rollback the KOI pipeline in case of deployment issues.

## Backup Locations

All backups are stored in: `~/backups/YYYYMMDD/`

- **Database**: `eliza_full_HHMMSS.sql` (1.4GB)
- **KOI Memories**: `koi_memories_HHMMSS.sql` (specific table)
- **Code**: `koi_code_HHMMSS.tar.gz`
- **Configuration**: `config/` directory and `.env.backup`

---

## Emergency Stop Procedures

### 1. Stop All Services Immediately
```bash
# Stop all KOI services
pkill -f "koi_event_bridge_v2.py"
pkill -f "bge_server.py"
pkill -f "koi_knowledge_mcp_server.py"
pkill -f "coordinator_fixed.py"

# Verify all stopped
ps aux | grep -E "koi|bge" | grep -v grep
```

### 2. Stop Using Systemd (if configured)
```bash
sudo systemctl stop koi-pipeline.service
sudo systemctl status koi-pipeline.service
```

---

## Database Rollback

### Full Database Restore
```bash
# 1. Find your backup
ls -lh ~/backups/*/eliza_full_*.sql

# 2. Stop all services that use the database
pkill -f "koi_event_bridge_v2.py"

# 3. Drop and recreate database (CAUTION!)
docker exec -it gaia-postgres-1 psql -U postgres -c "DROP DATABASE IF EXISTS eliza;"
docker exec -it gaia-postgres-1 psql -U postgres -c "CREATE DATABASE eliza;"

# 4. Restore from backup
docker exec -i gaia-postgres-1 psql -U postgres eliza < ~/backups/20250913/eliza_full_003153.sql

# 5. Verify restoration
docker exec gaia-postgres-1 psql -U postgres -d eliza -c "SELECT COUNT(*) FROM koi_memories;"
```

### KOI Memories Only Restore
```bash
# If you only need to restore KOI memories
docker exec gaia-postgres-1 psql -U postgres -d eliza -c "TRUNCATE TABLE koi_memories CASCADE;"
docker exec -i gaia-postgres-1 psql -U postgres -d eliza < ~/backups/20250913/koi_memories_003246.sql
```

---

## Code Rollback

### Full Code Restore
```bash
# 1. Find your code backup
ls -lh ~/backups/*/koi_code_*.tar.gz

# 2. Stop all services
bash /opt/projects/koi-processor/stop_all_services.sh 2>/dev/null || true

# 3. Backup current (broken) state
mv /opt/projects/koi-processor /opt/projects/koi-processor.broken
mv /opt/projects/koi-sensors /opt/projects/koi-sensors.broken

# 4. Extract backup
cd /opt/projects
tar -xzf ~/backups/20250913/koi_code_005225.tar.gz

# 5. Restore configuration
cp ~/backups/20250913/.env.backup /opt/projects/koi-processor/.env

# 6. Restart services
bash /opt/projects/koi-processor/start_all_services.sh
```

### Selective File Restore
```bash
# To restore specific files only
tar -tzf ~/backups/20250913/koi_code_005225.tar.gz | grep "filename"
tar -xzf ~/backups/20250913/koi_code_005225.tar.gz koi-processor/specific_file.py
```

---

## Git Rollback

### Revert Last Commit
```bash
# If you've already pushed
git revert HEAD
git push

# If you haven't pushed yet
git reset --hard HEAD~1
```

### Rollback to Specific Tag
```bash
# List available tags
git tag -l

# Checkout previous version
git checkout v0.9.0-pre-milestone-b

# Create new branch from stable point
git checkout -b hotfix/rollback-from-milestone-b
```

---

## Configuration Rollback

### Restore Environment Variables
```bash
cp ~/backups/20250913/.env.backup /opt/projects/koi-processor/.env
```

### Restore Service Configuration
```bash
cp -r ~/backups/20250913/config/* /opt/projects/koi-processor/config/
```

---

## Port Configuration Reset

If services are conflicting on ports:

```bash
# Find what's using each port
sudo lsof -i :8005  # Coordinator
sudo lsof -i :8090  # BGE Server
sudo lsof -i :8100  # Event Bridge
sudo lsof -i :8200  # MCP Server

# Kill specific process by PID
kill -9 <PID>

# Reset to default ports in .env
echo "COORDINATOR_PORT=8005" >> /opt/projects/koi-processor/.env
echo "EVENT_BRIDGE_PORT=8100" >> /opt/projects/koi-processor/.env
echo "BGE_SERVER_PORT=8090" >> /opt/projects/koi-processor/.env
echo "MCP_SERVER_PORT=8200" >> /opt/projects/koi-processor/.env
```

---

## Verification After Rollback

### 1. Check Service Health
```bash
curl http://localhost:8005/health  # Coordinator
curl http://localhost:8100/        # Event Bridge
curl http://localhost:8090/health  # BGE Server
curl http://localhost:8200/        # MCP Server
```

### 2. Verify Database
```bash
docker exec gaia-postgres-1 psql -U postgres -d eliza -c "
  SELECT 
    (SELECT COUNT(*) FROM koi_memories) as koi_memories,
    (SELECT COUNT(*) FROM memories) as agent_memories,
    (SELECT COUNT(*) FROM embeddings WHERE dim_1024 IS NOT NULL) as embeddings;
"
```

### 3. Run Validation
```bash
python3 /opt/projects/koi-processor/complete_validation.py
```

---

## Common Issues and Solutions

### Issue: Services won't start after rollback
```bash
# Clear any lock files
rm -f /tmp/*.lock

# Check for zombie processes
ps aux | grep defunct

# Restart Docker if needed
sudo systemctl restart docker
```

### Issue: Database connection errors
```bash
# Verify PostgreSQL is running
docker ps | grep postgres

# Check connection string
grep POSTGRES_URL /opt/projects/koi-processor/.env

# Test connection
docker exec gaia-postgres-1 psql -U postgres -c '\l'
```

### Issue: Missing dependencies after rollback
```bash
# Reinstall Python dependencies
cd /opt/projects/koi-processor
pip install -r requirements.txt

# Reinstall Node dependencies (if needed)
cd /opt/projects/koi-processor/bge-mcp-ts
npm install
```

---

## Emergency Contacts

If rollback fails, contact:
1. Check logs first: `/opt/projects/koi-processor/logs/`
2. Review monitoring: `bash /opt/projects/koi-processor/monitoring/production_monitor.sh`
3. Escalate to team lead with:
   - Error messages
   - Service status output
   - Last known good configuration

---

## Prevention

To avoid needing rollbacks:
1. Always backup before deployments
2. Test in staging environment first
3. Use feature flags for gradual rollout
4. Monitor services during deployment
5. Have rollback plan ready BEFORE deploying