#!/bin/bash

# Enhanced KOI Knowledge MCP Server launcher
# Provides both vector search and SPARQL capabilities

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Set environment variables
export POSTGRES_URL="${POSTGRES_URL:-postgresql://postgres:postgres@localhost:5433/eliza}"
export BGE_API_URL="${BGE_API_URL:-http://localhost:8090/encode}"
export FUSEKI_QUERY_ENDPOINT="${FUSEKI_QUERY_ENDPOINT:-http://localhost:3030/koi/sparql}"
export FUSEKI_UPDATE_ENDPOINT="${FUSEKI_UPDATE_ENDPOINT:-http://localhost:3030/koi/update}"

# Log configuration (only to stderr for MCP)
echo "[KOI-MCP] Starting enhanced MCP server..." >&2
echo "[KOI-MCP] PostgreSQL: $POSTGRES_URL" >&2
echo "[KOI-MCP] Fuseki: $FUSEKI_QUERY_ENDPOINT" >&2

# Run the enhanced server
exec bun "$SCRIPT_DIR/bge-server-enhanced.ts"