#!/usr/bin/env python3
"""
Direct test of MCP BGE server without ElizaOS
"""
import sys
import os
import time

# Test if the server can even start
print("Testing MCP BGE server startup...")

# Set environment
os.environ['POSTGRES_URL'] = 'postgresql://postgres:postgres@localhost:5433/eliza'

# Import and test initialization
try:
    from koi_mcp_bge_stdio_improved import BGESearchMCPServer
    import asyncio
    
    async def test_init():
        server = BGESearchMCPServer()
        print("Server created")
        
        # Try to initialize
        await server.initialize()
        print("Server initialized successfully!")
        
        # Test search
        result = await server.search_embeddings("regenerative agriculture", top_k=3)
        print(f"Search returned {result.get('count', 0)} results")
        
        # Cleanup
        await server.cleanup()
        print("Server cleaned up")
    
    # Run the test
    asyncio.run(test_init())
    print("\nDirect test passed!")
    
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()