#!/bin/bash
# KOI Pipeline Production Monitoring & Alerting Script
# Runs health checks and sends alerts for service failures

# Configuration
ALERT_EMAIL="${ALERT_EMAIL:-${KOI_ALERT_EMAIL:-}}"
SLACK_WEBHOOK="${SLACK_WEBHOOK:-}"
LOG_FILE="/opt/projects/koi-processor/logs/monitoring.log"

# Load optional alert config
if [ -z "$ALERT_EMAIL" ] && [ -f /opt/projects/koi-processor/.alert-config ]; then
    source /opt/projects/koi-processor/.alert-config
fi

# Mailer availability
HAS_MSMTP=false
if command -v msmtp &> /dev/null && { [ -f /etc/msmtprc ] || [ -f ~/.msmtprc ]; }; then
    HAS_MSMTP=true
fi
HAS_MAIL=false
if command -v mail &> /dev/null; then
    HAS_MAIL=true
fi

# Service endpoints
COORDINATOR_URL="http://localhost:8005/health"
EVENT_BRIDGE_URL="http://localhost:8100"
BGE_SERVER_URL="http://localhost:8090/health"
MCP_SERVER_URL="http://localhost:8200"
POSTGRES_HOST="localhost"
POSTGRES_PORT="5433"

# Alert thresholds
MAX_RESPONSE_TIME=10  # seconds
MIN_MEMORY_COUNT=20  # minimum expected KOI memories

# Colors for terminal output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Logging function
log_message() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

# Alert function
send_alert() {
    local service=$1
    local message=$2
    local severity=$3  # critical, warning, info
    
    log_message "ALERT [$severity]: $service - $message"
    
    # Send email alert if configured
    if [ -n "$ALERT_EMAIL" ]; then
        if [ "$HAS_MSMTP" = true ]; then
            cat << EOF | msmtp "$ALERT_EMAIL"
Subject: [KOI ALERT] $service [$severity]
From: zaldarren@gmail.com
To: $ALERT_EMAIL

$message
EOF
        elif [ "$HAS_MAIL" = true ]; then
            echo "$message" | mail -s "KOI Pipeline Alert: $service [$severity]" "$ALERT_EMAIL"
        else
            log_message "ALERT: No mailer configured for $service alert"
        fi
    fi
    
    # Send Slack alert if configured
    if [ -n "$SLACK_WEBHOOK" ]; then
        curl -X POST -H 'Content-type: application/json' \
            --data "{\"text\":\":warning: *KOI Pipeline Alert*\n*Service:* $service\n*Severity:* $severity\n*Message:* $message\"}" \
            "$SLACK_WEBHOOK" 2>/dev/null
    fi
}

# Check service health
check_service() {
    local name=$1
    local url=$2
    local expected_status=${3:-200}
    
    start_time=$(date +%s%N)
    response=$(curl -s -o /dev/null -w "%{http_code}" --max-time $MAX_RESPONSE_TIME "$url" 2>/dev/null)
    end_time=$(date +%s%N)
    
    response_time=$(( ($end_time - $start_time) / 1000000 ))  # Convert to milliseconds
    
    if [ "$response" == "$expected_status" ]; then
        echo -e "${GREEN}✓${NC} $name: OK (${response_time}ms)"
        return 0
    else
        echo -e "${RED}✗${NC} $name: FAILED (HTTP $response)"
        send_alert "$name" "Service not responding. HTTP status: $response" "critical"
        return 1
    fi
}

# Check PostgreSQL
check_postgres() {
    if docker exec gaia-postgres-1 psql -U postgres -d eliza -c '\l' &>/dev/null; then
        # Get memory counts
        koi_count=$(docker exec gaia-postgres-1 psql -U postgres -d eliza -t -c "SELECT COUNT(*) FROM koi_memories" 2>/dev/null | tr -d ' ')
        agent_count=$(docker exec gaia-postgres-1 psql -U postgres -d eliza -t -c "SELECT COUNT(*) FROM memories" 2>/dev/null | tr -d ' ')
        
        echo -e "${GREEN}✓${NC} PostgreSQL: OK (KOI: $koi_count, Agent: $agent_count)"
        
        # Check if KOI memory count is too low
        if [ "$koi_count" -lt "$MIN_MEMORY_COUNT" ]; then
            send_alert "PostgreSQL" "Low KOI memory count: $koi_count (threshold: $MIN_MEMORY_COUNT)" "warning"
        fi
        
        return 0
    else
        echo -e "${RED}✗${NC} PostgreSQL: FAILED"
        send_alert "PostgreSQL" "Database not accessible" "critical"
        return 1
    fi
}

# Check process status
check_process() {
    local name=$1
    local pattern=$2
    
    if pgrep -f "$pattern" > /dev/null; then
        pid=$(pgrep -f "$pattern" | head -1)
        echo -e "${GREEN}✓${NC} $name: Running (PID: $pid)"
        return 0
    else
        echo -e "${RED}✗${NC} $name: Not running"
        send_alert "$name" "Process not running" "critical"
        return 1
    fi
}

# Check disk space
check_disk_space() {
    usage=$(df -h /opt/projects | awk 'NR==2 {print $5}' | sed 's/%//')
    
    if [ "$usage" -gt 80 ]; then
        echo -e "${YELLOW}⚠${NC} Disk usage: ${usage}% (WARNING)"
        send_alert "Disk Space" "High disk usage: ${usage}%" "warning"
    elif [ "$usage" -gt 90 ]; then
        echo -e "${RED}✗${NC} Disk usage: ${usage}% (CRITICAL)"
        send_alert "Disk Space" "Critical disk usage: ${usage}%" "critical"
    else
        echo -e "${GREEN}✓${NC} Disk usage: ${usage}%"
    fi
}

# Check log errors
check_log_errors() {
    local log_file=$1
    local service=$2
    
    if [ -f "$log_file" ]; then
        recent_errors=$(tail -1000 "$log_file" | grep -i "error\|exception\|critical" | tail -5)
        error_count=$(tail -1000 "$log_file" | grep -c -i "error\|exception\|critical")
        
        if [ "$error_count" -gt 10 ]; then
            echo -e "${YELLOW}⚠${NC} $service logs: $error_count errors in last 1000 lines"
            send_alert "$service" "High error rate: $error_count errors in recent logs" "warning"
        else
            echo -e "${GREEN}✓${NC} $service logs: OK"
        fi
    fi
}

# Main monitoring function
main() {
    echo "========================================"
    echo "KOI Pipeline Production Monitor"
    echo "$(date '+%Y-%m-%d %H:%M:%S')"
    echo "========================================"
    
    # Initialize status
    all_ok=true
    
    # Check services
    echo -e "\n📊 Service Health:"
    check_service "KOI Coordinator" "$COORDINATOR_URL" || all_ok=false
    check_service "Event Bridge" "$EVENT_BRIDGE_URL" || all_ok=false
    check_service "BGE Server" "$BGE_SERVER_URL" || all_ok=false
    check_service "MCP Server" "$MCP_SERVER_URL" || all_ok=false
    
    # Check database
    echo -e "\n💾 Database:"
    check_postgres || all_ok=false
    
    # Check processes
    echo -e "\n⚙️ Processes:"
check_process "Coordinator" "run_coordinator.py"
    check_process "Event Bridge" "koi_event_bridge_v2.py"
    check_process "BGE Server" "bge_server.py"
    check_process "MCP Server" "koi_knowledge_mcp_server.py"
    
    # Check system resources
    echo -e "\n💿 System Resources:"
    check_disk_space
    
    # Check logs for errors
    echo -e "\n📝 Log Analysis:"
    check_log_errors "/opt/projects/koi-processor/logs/event_bridge.log" "Event Bridge"
    check_log_errors "/opt/projects/koi-processor/logs/bge_server.log" "BGE Server"
    
    # Summary
    echo -e "\n========================================"
    if [ "$all_ok" = true ]; then
        echo -e "${GREEN}✅ All systems operational${NC}"
        log_message "INFO: All systems operational"
    else
        echo -e "${RED}❌ Some systems need attention${NC}"
        log_message "WARNING: Some systems are not operational"
    fi
    echo "========================================"
}

# Run main function
main

# Exit with appropriate code
if [ "$all_ok" = true ]; then
    exit 0
else
    exit 1
fi
