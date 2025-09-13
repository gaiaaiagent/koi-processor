#!/bin/bash

echo "================================"
echo "Starting Milestone B Services"
echo "================================"

# Configuration
LOG_DIR="logs"
PYTHON_CMD="python3"

# Create log directory if it doesn't exist
mkdir -p $LOG_DIR

# Kill any existing services
echo "Stopping existing services..."
pkill -f "coordinator|event_bridge|bge_server|quality_pipeline|audio_pipeline" 2>/dev/null || true
sleep 2

# Function to start a service
start_service() {
    local name=$1
    local script=$2
    local log_file=$3
    
    echo -n "Starting $name..."
    nohup $PYTHON_CMD $script > $LOG_DIR/$log_file 2>&1 &
    local pid=$!
    sleep 1
    
    if ps -p $pid > /dev/null; then
        echo " ✓ (PID: $pid)"
    else
        echo " ✗ (failed to start - check $LOG_DIR/$log_file)"
    fi
}

# Start services in order
echo ""
echo "Starting core services..."

# 1. Start Coordinator (if exists)
if [ -f "coordinator.py" ]; then
    start_service "KOI Coordinator" "coordinator.py" "coordinator.log"
elif [ -f "koi_protocol/coordinator/run_coordinator.py" ]; then
    cd koi-sensors 2>/dev/null && \
    start_service "KOI Coordinator" "koi_protocol/coordinator/run_coordinator.py" "coordinator.log" && \
    cd .. 2>/dev/null
fi

# 2. Start BGE Server
if [ -f "bge_server.py" ]; then
    start_service "BGE Server" "bge_server.py" "bge_server.log"
fi

# 3. Start Event Bridge
if [ -f "koi_event_bridge_v2.py" ]; then
    start_service "Event Bridge v2" "koi_event_bridge_v2.py" "event_bridge.log"
elif [ -f "koi_event_bridge.py" ]; then
    start_service "Event Bridge" "koi_event_bridge.py" "event_bridge.log"
fi

# 4. Start Quality Pipeline (optional)
if [ -f "scripts/quality_pipeline.py" ]; then
    start_service "Quality Pipeline" "scripts/quality_pipeline.py" "quality.log"
fi

# 5. Start Audio Pipeline (optional)
if [ -f "audio_pipeline_enhanced.py" ]; then
    start_service "Audio Pipeline" "audio_pipeline_enhanced.py" "audio.log"
fi

# 6. Start any additional services
if [ -f "scheduler.py" ]; then
    start_service "Scheduler" "scheduler.py" "scheduler.log"
fi

# Wait for services to stabilize
echo ""
echo "Waiting for services to stabilize..."
sleep 5

# Check service status
echo ""
echo "Checking service status..."
echo "================================"

# Function to check service endpoint
check_endpoint() {
    local name=$1
    local url=$2
    
    echo -n "$name: "
    if curl -s -o /dev/null -w "%{http_code}" "$url" | grep -q "200\|302\|404"; then
        echo "✓ Running"
    else
        echo "✗ Not responding"
    fi
}

# Check endpoints
check_endpoint "BGE Server" "http://localhost:8090/health"
check_endpoint "Event Bridge" "http://localhost:8100/"
check_endpoint "Coordinator" "http://localhost:8000/"

# Show running processes
echo ""
echo "Running processes:"
ps aux | grep -E "coordinator|event_bridge|bge_server|quality|audio|scheduler" | grep -v grep | awk '{print "  -", $11, "(PID:", $2")"}'

echo ""
echo "================================"
echo "Services started!"
echo "Check logs/ directory for output"
echo "Run 'python3 test_milestone_b_complete.py' to verify"
echo ""
echo "To start KOI sensors (optional):"
echo "  cd /opt/projects/koi-sensors"
echo "  ./start_all_sensors.sh"
echo "================================"