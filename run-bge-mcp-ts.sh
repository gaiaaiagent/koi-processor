#!/bin/bash
# BGE MCP TypeScript Server Launcher Script
# This script starts the KOI-MCP BGE search server (TypeScript version) for ElizaOS integration

# Get the directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Set database connection (can be overridden by environment)
if [ -z "$POSTGRES_URL" ]; then
    export POSTGRES_URL="postgresql://postgres:postgres@localhost:5433/eliza"
fi

# Set BGE API URL (optional - will use mock embeddings if not available)
if [ -z "$BGE_API_URL" ]; then
    export BGE_API_URL="http://localhost:8090/encode"
fi

# Run the TypeScript MCP server with Bun
exec bun run "${SCRIPT_DIR}/bge-mcp-ts/bge-server.ts"