#!/usr/bin/env python3
"""
Test client for MCP BGE server
Tests the stdio communication with the MCP server
"""
import json
import asyncio
import subprocess
import os

async def test_mcp_server():
    """Test the MCP server with basic protocol messages"""
    
    # Start the MCP server
    env = os.environ.copy()
    env['POSTGRES_URL'] = 'postgresql://postgres:postgres@localhost:5433/eliza'
    
    print("Starting MCP server...")
    proc = await asyncio.create_subprocess_exec(
        'python3', '/Users/darrenzal/projects/RegenAI/koi-processor/koi_mcp_bge_stdio_improved.py',
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env
    )
    
    # Wait a bit for server to initialize
    await asyncio.sleep(8)
    
    # Send initialize request
    init_request = {
        "jsonrpc": "2.0",
        "method": "initialize",
        "params": {
            "protocolVersion": "0.1.0",
            "capabilities": {
                "roots": {}
            },
            "clientInfo": {
                "name": "test-client",
                "version": "1.0.0"
            }
        },
        "id": 1
    }
    
    print(f"Sending initialize request...")
    request_str = json.dumps(init_request) + '\n'
    proc.stdin.write(request_str.encode())
    await proc.stdin.drain()
    
    # Read response
    try:
        response_line = await asyncio.wait_for(proc.stdout.readline(), timeout=5.0)
        response = json.loads(response_line.decode())
        print(f"Initialize response: {json.dumps(response, indent=2)}")
    except asyncio.TimeoutError:
        print("Timeout waiting for initialize response")
        stderr = await proc.stderr.read()
        print(f"Server stderr: {stderr.decode()}")
    except Exception as e:
        print(f"Error reading response: {e}")
    
    # Send tools/list request
    list_request = {
        "jsonrpc": "2.0",
        "method": "tools/list",
        "params": {},
        "id": 2
    }
    
    print(f"\nSending tools/list request...")
    request_str = json.dumps(list_request) + '\n'
    proc.stdin.write(request_str.encode())
    await proc.stdin.drain()
    
    # Read response
    try:
        response_line = await asyncio.wait_for(proc.stdout.readline(), timeout=5.0)
        response = json.loads(response_line.decode())
        print(f"Tools list response: {json.dumps(response, indent=2)}")
    except asyncio.TimeoutError:
        print("Timeout waiting for tools list response")
    except Exception as e:
        print(f"Error reading response: {e}")
    
    # Test search
    search_request = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {
            "name": "bge_search",
            "arguments": {
                "query": "regenerative agriculture",
                "top_k": 3
            }
        },
        "id": 3
    }
    
    print(f"\nSending search request...")
    request_str = json.dumps(search_request) + '\n'
    proc.stdin.write(request_str.encode())
    await proc.stdin.drain()
    
    # Read response
    try:
        response_line = await asyncio.wait_for(proc.stdout.readline(), timeout=10.0)
        response = json.loads(response_line.decode())
        print(f"Search response: {json.dumps(response, indent=2)[:500]}...")
    except asyncio.TimeoutError:
        print("Timeout waiting for search response")
    except Exception as e:
        print(f"Error reading response: {e}")
    
    # Cleanup
    proc.terminate()
    await proc.wait()
    print("\nTest complete")

if __name__ == "__main__":
    asyncio.run(test_mcp_server())