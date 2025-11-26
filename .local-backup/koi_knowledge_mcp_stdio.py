#!/usr/bin/env python3
"""
KOI Knowledge MCP Server - stdio Transport
Implements MCP protocol (2024-11-05) over stdin/stdout
Wraps the KOI Hybrid RAG API for knowledge retrieval
"""

import sys
import json
import httpx
from typing import Dict, Any, List

# API Configuration
HYBRID_RAG_API_URL = "http://202.61.196.119:8301/api/koi/query"
DEFAULT_TIMEOUT = 30.0

def log_error(message: str, **kwargs):
    """Log errors to stderr (not stdout - stdout is for JSON-RPC only)"""
    error_data = {"error": message, **kwargs}
    print(json.dumps(error_data), file=sys.stderr, flush=True)


def create_response(request_id: Any, result: Dict[str, Any]) -> Dict[str, Any]:
    """Create a JSON-RPC success response"""
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": result
    }


def create_error_response(request_id: Any, code: int, message: str) -> Dict[str, Any]:
    """Create a JSON-RPC error response"""
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {
            "code": code,
            "message": message
        }
    }


def handle_initialize(request_id: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Handle MCP initialize request"""
    log_error("Handling initialize request", params=params)

    result = {
        "protocolVersion": "2024-11-05",
        "capabilities": {
            "tools": {
                "listChanged": False
            }
        },
        "serverInfo": {
            "name": "koi-knowledge",
            "version": "3.0.0"
        },
        "instructions": "KOI Knowledge Graph - Search 6,500+ documents about regenerative agriculture, Regen Network, and ecological credits using hybrid RAG (RRF + BGE embeddings + keyword search) with confidence-based results."
    }

    return create_response(request_id, result)


def handle_tools_list(request_id: Any) -> Dict[str, Any]:
    """Handle MCP tools/list request"""
    log_error("Handling tools/list request")

    tools = [
        {
            "name": "search_knowledge",
            "description": "Search the KOI knowledge base using hybrid RAG (RRF + BGE embeddings + BM25 keyword search). Returns relevant documents with confidence scores and RIDs for citation.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query or question"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of results to return (default: 5)",
                        "default": 5
                    }
                },
                "required": ["query"]
            }
        },
        {
            "name": "get_memory",
            "description": "Retrieve a specific document by its RID (Resource Identifier). Use this to get the full content of a document referenced in search results.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "rid": {
                        "type": "string",
                        "description": "The RID of the document to retrieve (e.g., orn:web.page:registry.regen.network/abc123#chunk0)"
                    }
                },
                "required": ["rid"]
            }
        },
        {
            "name": "get_stats",
            "description": "Get statistics about the KOI knowledge base, including total documents, coverage areas, and system status.",
            "inputSchema": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    ]

    result = {"tools": tools}
    return create_response(request_id, result)


def call_hybrid_rag_api(question: str, limit: int = 5) -> Dict[str, Any]:
    """Call the production Hybrid RAG API"""
    try:
        with httpx.Client(timeout=DEFAULT_TIMEOUT) as client:
            response = client.post(
                HYBRID_RAG_API_URL,
                json={
                    "question": question,
                    "limit": limit,
                    "search_method": "hybrid"
                }
            )
            response.raise_for_status()
            return response.json()
    except Exception as e:
        log_error(f"API call failed: {str(e)}")
        raise


def format_search_results(api_response: Dict[str, Any]) -> str:
    """Format Hybrid RAG API response for MCP tool result"""
    results = api_response.get("results", [])
    confidence = api_response.get("confidence", 0.0)
    total_results = api_response.get("total_results", 0)

    if not results:
        return "No results found for your query."

    # Format results with markdown
    formatted = f"# Search Results (Confidence: {confidence:.3f})\n\n"
    formatted += f"Found {total_results} relevant documents:\n\n"

    for i, result in enumerate(results, 1):
        rid = result.get("rid", "unknown")
        content = result.get("content", "")
        score = result.get("score", 0.0)

        # Truncate content if very long
        if len(content) > 500:
            content = content[:500] + "..."

        formatted += f"## Result {i} (Score: {score:.3f})\n"
        formatted += f"**RID**: `{rid}`\n\n"
        formatted += f"{content}\n\n"
        formatted += "---\n\n"

    return formatted


def handle_search_knowledge(request_id: Any, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Handle search_knowledge tool call"""
    query = arguments.get("query")
    limit = arguments.get("limit", 5)

    if not query:
        return create_error_response(request_id, -32602, "Missing required parameter: query")

    log_error(f"Searching knowledge base", query=query, limit=limit)

    try:
        api_response = call_hybrid_rag_api(query, limit)
        formatted_text = format_search_results(api_response)

        result = {
            "content": [
                {
                    "type": "text",
                    "text": formatted_text
                }
            ]
        }

        return create_response(request_id, result)

    except Exception as e:
        log_error(f"Search failed: {str(e)}")
        return create_error_response(request_id, -32603, f"Search failed: {str(e)}")


def handle_get_memory(request_id: Any, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Handle get_memory tool call"""
    rid = arguments.get("rid")

    if not rid:
        return create_error_response(request_id, -32602, "Missing required parameter: rid")

    log_error(f"Retrieving document by RID", rid=rid)

    # For now, return a placeholder - this would need a dedicated API endpoint
    result = {
        "content": [
            {
                "type": "text",
                "text": f"Document retrieval by RID not yet implemented. RID requested: {rid}\n\nTo get full document content, use search_knowledge with specific terms from this RID."
            }
        ]
    }

    return create_response(request_id, result)


def handle_get_stats(request_id: Any) -> Dict[str, Any]:
    """Handle get_stats tool call"""
    log_error("Retrieving knowledge base statistics")

    stats_text = """# KOI Knowledge Base Statistics

**Total Documents**: 6,500+
**Total Embeddings**: 49,000+
**Coverage Areas**:
- Regenerative Agriculture
- Regen Network Governance
- Ecological Methodologies (VCS, GHG, etc.)
- Carbon & Biodiversity Credits
- Community Discussions & Proposals

**Search Methods**:
- Hybrid RAG (RRF + BGE embeddings + BM25)
- Confidence-based ranking
- RID-based citation system

**Status**: ✅ Operational
"""

    result = {
        "content": [
            {
                "type": "text",
                "text": stats_text
            }
        ]
    }

    return create_response(request_id, result)


def handle_tools_call(request_id: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Handle MCP tools/call request"""
    tool_name = params.get("name")
    arguments = params.get("arguments", {})

    log_error(f"Calling tool: {tool_name}", arguments=arguments)

    if tool_name == "search_knowledge":
        return handle_search_knowledge(request_id, arguments)
    elif tool_name == "get_memory":
        return handle_get_memory(request_id, arguments)
    elif tool_name == "get_stats":
        return handle_get_stats(request_id)
    else:
        return create_error_response(request_id, -32601, f"Unknown tool: {tool_name}")


def handle_request(request: Dict[str, Any]) -> Dict[str, Any]:
    """Handle a JSON-RPC request and return a JSON-RPC response"""
    request_id = request.get("id")
    method = request.get("method")
    params = request.get("params", {})

    log_error(f"Received request", method=method, id=request_id)

    try:
        if method == "initialize":
            return handle_initialize(request_id, params)
        elif method == "tools/list":
            return handle_tools_list(request_id)
        elif method == "tools/call":
            return handle_tools_call(request_id, params)
        else:
            return create_error_response(request_id, -32601, f"Method not found: {method}")

    except Exception as e:
        log_error(f"Request handling failed: {str(e)}", method=method)
        return create_error_response(request_id, -32603, f"Internal error: {str(e)}")


def main():
    """Main stdio loop - read JSON-RPC requests from stdin, write responses to stdout"""
    log_error("KOI Knowledge MCP Server starting (stdio transport)")

    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue

            try:
                request = json.loads(line)
                response = handle_request(request)

                # Write response to stdout (one JSON object per line)
                print(json.dumps(response), flush=True)

            except json.JSONDecodeError as e:
                log_error(f"Invalid JSON received: {str(e)}", line=line)
                error_response = create_error_response(None, -32700, "Parse error")
                print(json.dumps(error_response), flush=True)

    except KeyboardInterrupt:
        log_error("Server interrupted by user")
    except Exception as e:
        log_error(f"Fatal error: {str(e)}")
        sys.exit(1)

    log_error("KOI Knowledge MCP Server shutting down")


if __name__ == "__main__":
    main()
