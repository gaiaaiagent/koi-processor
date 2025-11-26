#!/bin/bash
# KOI Knowledge MCP Server - stdio transport launcher
# This script ensures the server runs from the correct directory with venv

cd "$(dirname "$0")"
source venv/bin/activate
exec python3 src/core/koi_knowledge_mcp_stdio.py
