# Recent Changes - KOI Processor (September 13, 2025)

## Fixed Issues
1. **Systemd Service**: Updated `systemd/koi-coordinator.service` to use correct coordinator path
   - Changed from non-existent `coordinator_fixed.py` 
   - Now correctly points to `/opt/projects/koi-sensors/koi_protocol/coordinator/run_coordinator.py`
   - Added PYTHONPATH environment variable

## Modified Files
- `start_all_services.sh`: Added instructions for starting sensors separately
- `systemd/koi-coordinator.service`: Fixed coordinator path and working directory

## Current Pipeline Status
- ✅ Event Bridge v2 running on port 8100
- ✅ BGE Server running on port 8090  
- ✅ PostgreSQL with pgvector on port 5433
- ✅ Coordinator receiving events from sensors
- ⚠️ Events lack content data (showing "0 chunks, 0 embeddings")

## How to Start Services
```bash
# Start all pipeline services
./start_all_services.sh

# Check status
tail -f logs/*.log

# Start sensors (in koi-sensors directory)
cd /opt/projects/koi-sensors
./start_all_sensors.sh
```