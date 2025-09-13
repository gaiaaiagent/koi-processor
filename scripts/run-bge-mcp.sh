#!/bin/bash
# BGE MCP Server Launcher Script
# This script starts the KOI-MCP BGE search server for ElizaOS integration

# Get the directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Set Python environment (adjust if using virtual environment)
export PYTHONPATH="${SCRIPT_DIR}:${PYTHONPATH}"

# Set database connection (can be overridden by environment)
if [ -z "$POSTGRES_URL" ]; then
    export POSTGRES_URL="postgresql://postgres:postgres@localhost:5433/eliza"
fi

# Run the improved MCP server with pre-initialization
exec python3 "${SCRIPT_DIR}/koi_mcp_bge_stdio_final.py"