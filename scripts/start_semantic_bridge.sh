#!/bin/bash
# Start the KOI Semantic Event Bridge
# Used by PM2 and systemd to launch the extraction pipeline

cd /opt/projects/koi-processor
source .env
source venv/bin/activate
export PYTHONPATH=/opt/projects/koi-processor/src:/opt/projects/koi-processor
exec python src/core/koi_event_bridge_semantic.py
