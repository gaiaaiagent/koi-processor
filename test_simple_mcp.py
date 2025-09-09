#!/usr/bin/env python3
"""Simple test MCP server to debug connection issues"""
import asyncio
import json
from mcp import ServerSession, stdio_server
from mcp.types import TextContent

async def main():
    """Main entry point for simple test server"""
    async with stdio_server() as (read_stream, write_stream):
        async with ServerSession(read_stream, write_stream) as session:
            # Register a simple test tool
            @session.list_tools()
            async def list_tools():
                return [
                    {
                        "name": "test_echo",
                        "description": "Echo back a test message",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "message": {"type": "string"}
                            },
                            "required": ["message"]
                        }
                    }
                ]
            
            @session.call_tool()
            async def call_tool(name: str, arguments: dict):
                if name == "test_echo":
                    return [TextContent(
                        type="text",
                        text=f"Echo: {arguments.get('message', 'No message')}"
                    )]
                return []
            
            # Initialize and run
            await session.initialize({
                "name": "test-simple-mcp",
                "version": "1.0.0"
            })
            
            await session.run()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        import sys
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)