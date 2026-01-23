#!/bin/bash
# Full extraction script - runs all repositories
# Started: Thu Jan 22 17:15:22 PST 2026

cd /opt/projects/koi-processor
source .venv/bin/activate
set -a && source .env && set +a

LOG_DIR=/opt/projects/koi-processor/logs
mkdir -p $LOG_DIR
MAIN_LOG=$LOG_DIR/full_extraction_$(date +%Y%m%d_%H%M%S).log

echo "=== FULL EXTRACTION STARTED at $(date) ===" | tee -a $MAIN_LOG

# List of repositories to extract
declare -A REPOS
REPOS["koi-processor"]="/opt/projects/koi-processor"
REPOS["koi-sensors"]="/opt/projects/koi-sensors"
REPOS["koi-research"]="/opt/projects/koi-research"
REPOS["regen-ledger"]="/opt/projects/regen-repos/regen-ledger"
REPOS["regen-web"]="/opt/projects/regen-repos/regen-web"
REPOS["regen-koi-mcp"]="/opt/projects/regen-koi-mcp"
REPOS["regen-data-standards"]="/opt/projects/regen-repos/regen-data-standards"

for repo in "${!REPOS[@]}"; do
    path="${REPOS[$repo]}"
    echo "" | tee -a $MAIN_LOG
    echo "=== Extracting $repo from $path ===" | tee -a $MAIN_LOG
    echo "Started at $(date)" | tee -a $MAIN_LOG
    
    if [ -d "$path" ]; then
        python scripts/load_to_staging.py --repo "$repo" --path "$path" 2>&1 | tee -a $MAIN_LOG
        echo "Finished $repo at $(date)" | tee -a $MAIN_LOG
    else
        echo "WARNING: Path $path does not exist, skipping $repo" | tee -a $MAIN_LOG
    fi
done

# Run cross-file IMPLEMENTS detection
echo "" | tee -a $MAIN_LOG
echo "=== Running cross-file IMPLEMENTS detection ===" | tee -a $MAIN_LOG
python scripts/detect_cross_file_implements.py 2>&1 | tee -a $MAIN_LOG

echo "" | tee -a $MAIN_LOG
echo "=== FULL EXTRACTION COMPLETED at $(date) ===" | tee -a $MAIN_LOG
echo "Log file: $MAIN_LOG"
