#!/bin/bash

# KOI Processor Setup Script
# This script sets up the complete KOI processor environment

set -e  # Exit on error

echo "=========================================="
echo "KOI Processor Setup"
echo "=========================================="

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${GREEN}✓${NC} $1"
}

print_error() {
    echo -e "${RED}✗${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

# Check Python version
echo "Checking Python version..."
python_version=$(python3 --version 2>&1 | grep -oE '[0-9]+\.[0-9]+')
required_version="3.8"

if [ "$(printf '%s\n' "$required_version" "$python_version" | sort -V | head -n1)" = "$required_version" ]; then 
    print_status "Python $python_version is installed (>= 3.8 required)"
else
    print_error "Python 3.8 or higher is required. Found: $python_version"
    exit 1
fi

# Check PostgreSQL
echo "Checking PostgreSQL..."
if command -v psql &> /dev/null; then
    print_status "PostgreSQL client is installed"
else
    print_warning "PostgreSQL client not found. Please install PostgreSQL."
fi

# Create virtual environment
echo "Setting up Python virtual environment..."
if [ ! -d "venv" ]; then
    python3 -m venv venv
    print_status "Virtual environment created"
else
    print_status "Virtual environment already exists"
fi

# Activate virtual environment
source venv/bin/activate
print_status "Virtual environment activated"

# Upgrade pip
echo "Upgrading pip..."
pip install --upgrade pip --quiet
print_status "pip upgraded"

# Install requirements
echo "Installing Python dependencies..."
pip install -r requirements.txt --quiet
print_status "Python dependencies installed"

# Create .env file if it doesn't exist
if [ ! -f ".env" ]; then
    echo "Creating .env file from template..."
    cp .env.example .env
    print_status ".env file created"
    print_warning "Please edit .env file with your configuration"
else
    print_status ".env file already exists"
fi

# Check database connection
echo "Checking database connection..."
if [ -z "$POSTGRES_URL" ]; then
    source .env
fi

if python3 -c "import asyncpg, asyncio; asyncio.run(asyncpg.connect('$POSTGRES_URL').close())" 2>/dev/null; then
    print_status "Database connection successful"
else
    print_warning "Could not connect to database. Please check POSTGRES_URL in .env"
fi

# Create logs directory
if [ ! -d "logs" ]; then
    mkdir logs
    print_status "Created logs directory"
fi

# Setup MCP server (if bun is installed)
if command -v bun &> /dev/null; then
    echo "Setting up MCP server..."
    if [ -d "bge-mcp-ts" ]; then
        cd bge-mcp-ts
        bun install --silent
        cd ..
        print_status "MCP server dependencies installed"
    else
        print_warning "bge-mcp-ts directory not found"
    fi
else
    print_warning "Bun not installed. Skipping MCP server setup."
    echo "     Install Bun from: https://bun.sh"
fi

echo ""
echo "=========================================="
echo "Setup Complete!"
echo "=========================================="
echo ""
echo "Next steps:"
echo "1. Edit .env file with your configuration"
echo "2. Run database migrations:"
echo "   psql -U postgres -d eliza < migrations/001_create_transformation_receipts.sql"
echo "   psql -U postgres -d eliza < migrations/002_create_agent_knowledge_permissions.sql"
echo "   psql -U postgres -d eliza < migrations/003_create_isolated_koi_tables.sql"
echo ""
echo "3. Start services:"
echo "   python bge_server.py                    # BGE embedding server"
echo "   python koi_event_bridge_v2.py          # Event processing bridge"
echo "   cd bge-mcp-ts && bun run bge-server.ts # MCP search server (optional)"
echo ""
echo "For more information, see README.md"