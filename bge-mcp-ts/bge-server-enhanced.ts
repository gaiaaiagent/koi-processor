#!/usr/bin/env bun
/**
 * Enhanced BGE MCP Server with SPARQL and Hybrid Search
 * Provides semantic search, SPARQL queries, and hybrid knowledge capabilities
 */

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";
import { Pool } from "pg";
import axios from "axios";
import fetch from "node-fetch";

// Server configuration
const BGE_API_URL = process.env.BGE_API_URL || "http://localhost:8090/encode";
const POSTGRES_URL = process.env.POSTGRES_URL || "postgresql://postgres:postgres@localhost:5433/eliza";
const FUSEKI_QUERY_ENDPOINT = process.env.FUSEKI_QUERY_ENDPOINT || "http://localhost:3030/koi/sparql";
const FUSEKI_UPDATE_ENDPOINT = process.env.FUSEKI_UPDATE_ENDPOINT || "http://localhost:3030/koi/update";

// Parse PostgreSQL connection string
function parsePostgresUrl(url: string) {
  const match = url.match(/postgres(?:ql)?:\/\/([^:]+):([^@]+)@([^:]+):(\d+)\/(.+)/);
  if (!match) throw new Error("Invalid PostgreSQL URL");
  return {
    user: match[1],
    password: match[2],
    host: match[3],
    port: parseInt(match[4]),
    database: match[5]
  };
}

// Initialize PostgreSQL connection
const dbConfig = parsePostgresUrl(POSTGRES_URL);
const pool = new Pool(dbConfig);

// Test database connection
async function testConnection() {
  try {
    const result = await pool.query("SELECT COUNT(*) FROM koi_embeddings WHERE dim_1024 IS NOT NULL");
    console.error(`[BGE-MCP] Connected to database. Found ${result.rows[0].count} KOI BGE embeddings`);
    return true;
  } catch (error) {
    console.error("[BGE-MCP] Database connection failed:", error);
    return false;
  }
}

// Test Fuseki connection
async function testFusekiConnection() {
  try {
    const response = await fetch(FUSEKI_QUERY_ENDPOINT, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/sparql-query',
        'Accept': 'application/sparql-results+json'
      },
      body: 'ASK { ?s ?p ?o }'
    });

    if (response.ok) {
      console.error("[BGE-MCP] Connected to Apache Jena Fuseki");
      return true;
    }
    return false;
  } catch (error) {
    console.error("[BGE-MCP] Fuseki connection failed:", error);
    return false;
  }
}

// Generate BGE embedding using Python service or API
async function generateEmbedding(text: string): Promise<number[]> {
  try {
    const response = await axios.post(BGE_API_URL, { text }, { timeout: 30000 });
    return response.data.embedding;
  } catch (error) {
    console.error("[BGE-MCP] Warning: BGE service not available, using mock embedding");
    const hash = text.split('').reduce((acc, char) => acc + char.charCodeAt(0), 0);
    const embedding = new Array(1024).fill(0).map((_, i) =>
      Math.sin((hash + i) * 0.1) * 0.1
    );
    return embedding;
  }
}

// Execute SPARQL query
async function executeSparqlQuery(query: string) {
  try {
    const response = await fetch(FUSEKI_QUERY_ENDPOINT, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/sparql-query',
        'Accept': 'application/sparql-results+json'
      },
      body: query
    });

    if (!response.ok) {
      throw new Error(`SPARQL query failed: ${response.statusText}`);
    }

    const result = await response.json();
    return result;
  } catch (error: any) {
    console.error("[BGE-MCP] SPARQL query error:", error);
    throw error;
  }
}

// Search for similar embeddings
async function searchEmbeddings(
  query: string,
  topK: number = 10
) {
  try {
    // Generate query embedding
    const queryEmbedding = await generateEmbedding(query);
    const embeddingStr = '[' + queryEmbedding.join(',') + ']';

    // Search in KOI memories with embeddings
    let sql = `
      SELECT
        km.id,
        km.rid,
        km.content,
        km.metadata,
        km.source_sensor,
        km.version,
        1 - (ke.dim_1024 <=> $1::vector) as similarity
      FROM koi_memories km
      JOIN koi_embeddings ke ON ke.memory_id = km.id
      WHERE ke.dim_1024 IS NOT NULL
      ORDER BY similarity DESC
      LIMIT $2
    `;

    const result = await pool.query(sql, [embeddingStr, topK]);

    // Format results
    const results = result.rows.map(row => {
      const content = typeof row.content === 'string' ? JSON.parse(row.content) : row.content;
      const text = content.text || content.content || JSON.stringify(content);

      return {
        text: text.substring(0, 1000),
        metadata: {
          rid: row.rid,
          source_sensor: row.source_sensor,
          version: row.version,
          similarity: parseFloat(row.similarity),
          ...(row.metadata || {})
        }
      };
    });

    return {
      query,
      count: results.length,
      results
    };
  } catch (error) {
    console.error("[BGE-MCP] Search failed:", error);
    throw error;
  }
}

// Execute SPARQL query for entities
async function queryEntities(
  entityType?: string,
  limit: number = 20
) {
  try {
    let query = `
      PREFIX regen: <https://regen.network/ontology#>
      PREFIX koi: <https://regen.network/koi#>
      PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

      SELECT ?entity ?type ?label ?description WHERE {
        ?entity a ?type .
        OPTIONAL { ?entity rdfs:label ?label }
        OPTIONAL { ?entity rdfs:comment ?description }
    `;

    if (entityType) {
      query += `
        FILTER(?type = regen:${entityType} || ?type = koi:${entityType})
      `;
    }

    query += `
      } LIMIT ${limit}
    `;

    const result = await executeSparqlQuery(query);
    return result;
  } catch (error) {
    console.error("[BGE-MCP] Entity query failed:", error);
    throw error;
  }
}

// Explore knowledge graph relationships
async function exploreGraph(
  startEntity: string,
  depth: number = 2,
  limit: number = 50
) {
  try {
    const query = `
      PREFIX regen: <https://regen.network/ontology#>
      PREFIX koi: <https://regen.network/koi#>
      PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

      SELECT DISTINCT ?subject ?predicate ?object ?subjectLabel ?objectLabel WHERE {
        {
          # Outgoing relationships
          BIND(<${startEntity}> AS ?subject)
          ?subject ?predicate ?object .
          OPTIONAL { ?subject rdfs:label ?subjectLabel }
          OPTIONAL { ?object rdfs:label ?objectLabel }
        }
        UNION
        {
          # Incoming relationships
          BIND(<${startEntity}> AS ?object)
          ?subject ?predicate ?object .
          OPTIONAL { ?subject rdfs:label ?subjectLabel }
          OPTIONAL { ?object rdfs:label ?objectLabel }
        }

        FILTER(!isBlank(?object))
        FILTER(?predicate != rdf:type)
      } LIMIT ${limit}
    `;

    const result = await executeSparqlQuery(query);
    return result;
  } catch (error) {
    console.error("[BGE-MCP] Graph exploration failed:", error);
    throw error;
  }
}

// Hybrid search combining vector similarity and SPARQL
async function hybridSearch(
  query: string,
  sparqlFilter?: string,
  topK: number = 10
) {
  try {
    // Get semantic search results
    const semanticResults = await searchEmbeddings(query, topK * 2);

    // If SPARQL filter provided, get graph matches
    let graphMatches = new Set<string>();
    if (sparqlFilter) {
      const sparqlQuery = `
        PREFIX regen: <https://regen.network/ontology#>
        PREFIX koi: <https://regen.network/koi#>

        SELECT DISTINCT ?rid WHERE {
          ${sparqlFilter}
          ?entity koi:hasRID ?rid .
        } LIMIT ${topK * 2}
      `;

      try {
        const graphResult = await executeSparqlQuery(sparqlQuery);
        if (graphResult.results?.bindings) {
          graphResult.results.bindings.forEach((binding: any) => {
            if (binding.rid?.value) {
              graphMatches.add(binding.rid.value);
            }
          });
        }
      } catch (e) {
        console.error("[BGE-MCP] SPARQL filter failed, using semantic only:", e);
      }
    }

    // Combine and rank results
    const combinedResults = semanticResults.results.map(result => {
      const inGraph = graphMatches.has(result.metadata.rid);
      return {
        ...result,
        metadata: {
          ...result.metadata,
          in_graph: inGraph,
          // Boost score if in graph
          combined_score: result.metadata.similarity * (inGraph ? 1.5 : 1.0)
        }
      };
    });

    // Sort by combined score and limit
    combinedResults.sort((a, b) =>
      b.metadata.combined_score - a.metadata.combined_score
    );

    return {
      query,
      sparql_filter: sparqlFilter || "none",
      count: Math.min(topK, combinedResults.length),
      results: combinedResults.slice(0, topK)
    };
  } catch (error) {
    console.error("[BGE-MCP] Hybrid search failed:", error);
    throw error;
  }
}

// Generate default SPARQL from natural language
function generateDefaultSparqlFromNL(question: string): string {
  const questionLower = question.toLowerCase();

  // Look for entity type keywords
  if (questionLower.includes('agent')) {
    return `
      PREFIX regen: <https://regen.network/ontology#>
      PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

      SELECT ?agent ?name ?type WHERE {
        ?agent a regen:Agent .
        OPTIONAL { ?agent rdfs:label ?name }
        OPTIONAL { ?agent a ?type }
      } LIMIT 20
    `;
  }

  if (questionLower.includes('claim')) {
    return `
      PREFIX regen: <https://regen.network/ontology#>
      PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

      SELECT ?claim ?label ?evidence WHERE {
        ?claim a regen:Claim .
        OPTIONAL { ?claim rdfs:label ?label }
        OPTIONAL { ?claim regen:hasEvidence ?evidence }
      } LIMIT 20
    `;
  }

  if (questionLower.includes('document')) {
    return `
      PREFIX koi: <https://regen.network/koi#>
      PREFIX dc: <http://purl.org/dc/elements/1.1/>

      SELECT ?doc ?title ?type WHERE {
        ?doc a koi:Document .
        OPTIONAL { ?doc dc:title ?title }
        OPTIONAL { ?doc a ?type }
      } LIMIT 20
    `;
  }

  if (questionLower.includes('relationship') || questionLower.includes('relation')) {
    return `
      PREFIX regen: <https://regen.network/ontology#>
      PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>

      SELECT ?subject ?predicate ?object WHERE {
        ?subject ?predicate ?object .
        FILTER(?predicate != rdf:type)
        FILTER(STRSTARTS(STR(?predicate), STR(regen:)))
      } LIMIT 50
    `;
  }

  // Default: Get all entities
  return `
    PREFIX regen: <https://regen.network/ontology#>
    PREFIX koi: <https://regen.network/koi#>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

    SELECT ?entity ?type ?label WHERE {
      ?entity a ?type .
      OPTIONAL { ?entity rdfs:label ?label }
      FILTER(STRSTARTS(STR(?type), STR(regen:)) || STRSTARTS(STR(?type), STR(koi:)))
    } LIMIT 20
  `;
}

// Get statistics about KOI knowledge
async function getKnowledgeStats() {
  try {
    // Database stats
    const dbStatsResult = await pool.query(`
      SELECT
        COUNT(DISTINCT km.id) as total_memories,
        COUNT(DISTINCT ke.id) as memories_with_embeddings,
        COUNT(DISTINCT km.source_sensor) as unique_sensors,
        MIN(km.created_at) as oldest_memory,
        MAX(km.created_at) as newest_memory
      FROM koi_memories km
      LEFT JOIN koi_embeddings ke ON ke.memory_id = km.id
      WHERE ke.dim_1024 IS NOT NULL
    `);

    // SPARQL stats
    let graphStats = {
      total_triples: 0,
      entity_types: [],
      relationship_types: []
    };

    try {
      const triplesQuery = `
        SELECT (COUNT(*) as ?count) WHERE { ?s ?p ?o }
      `;
      const triplesResult = await executeSparqlQuery(triplesQuery);
      if (triplesResult.results?.bindings?.[0]?.count) {
        graphStats.total_triples = parseInt(triplesResult.results.bindings[0].count.value);
      }

      const typesQuery = `
        PREFIX regen: <https://regen.network/ontology#>
        PREFIX koi: <https://regen.network/koi#>

        SELECT DISTINCT ?type (COUNT(?entity) as ?count) WHERE {
          ?entity a ?type .
          FILTER(STRSTARTS(STR(?type), STR(regen:)) || STRSTARTS(STR(?type), STR(koi:)))
        } GROUP BY ?type ORDER BY DESC(?count) LIMIT 10
      `;
      const typesResult = await executeSparqlQuery(typesQuery);
      if (typesResult.results?.bindings) {
        graphStats.entity_types = typesResult.results.bindings.map((b: any) => ({
          type: b.type.value.split('#').pop(),
          count: parseInt(b.count.value)
        }));
      }
    } catch (e) {
      console.error("[BGE-MCP] Could not fetch graph stats:", e);
    }

    return {
      vector_store: {
        total_memories: parseInt(dbStatsResult.rows[0].total_memories),
        memories_with_embeddings: parseInt(dbStatsResult.rows[0].memories_with_embeddings),
        unique_sensors: parseInt(dbStatsResult.rows[0].unique_sensors),
        oldest_memory: dbStatsResult.rows[0].oldest_memory,
        newest_memory: dbStatsResult.rows[0].newest_memory,
        embedding_dimension: 1024,
        model: "BAAI/bge-large-en-v1.5"
      },
      knowledge_graph: graphStats
    };
  } catch (error) {
    console.error("[BGE-MCP] Stats query failed:", error);
    throw error;
  }
}

// Main server setup
async function main() {
  // Test connections
  const dbConnected = await testConnection();
  const fusekiConnected = await testFusekiConnection();

  if (!dbConnected) {
    console.error("[BGE-MCP] Failed to connect to database. Exiting.");
    process.exit(1);
  }

  if (!fusekiConnected) {
    console.error("[BGE-MCP] Warning: Fuseki not available. SPARQL features disabled.");
  }

  // Create MCP server
  const server = new Server(
    {
      name: "koi-knowledge",
      version: "2.0.0",
    },
    {
      capabilities: {
        tools: {},
      },
    }
  );

  // Register tools list handler
  server.setRequestHandler(ListToolsRequestSchema, async () => ({
    tools: [
      {
        name: "bge_search",
        description: "Search KOI knowledge using BGE semantic embeddings",
        inputSchema: {
          type: "object",
          properties: {
            query: {
              type: "string",
              description: "The search query"
            },
            top_k: {
              type: "number",
              description: "Number of results to return",
              default: 10
            }
          },
          required: ["query"]
        }
      },
      {
        name: "sparql_query",
        description: "Execute SPARQL query on the knowledge graph",
        inputSchema: {
          type: "object",
          properties: {
            query: {
              type: "string",
              description: "SPARQL query to execute"
            }
          },
          required: ["query"]
        }
      },
      {
        name: "hybrid_search",
        description: "Hybrid search combining semantic vectors and knowledge graph",
        inputSchema: {
          type: "object",
          properties: {
            query: {
              type: "string",
              description: "Natural language search query"
            },
            sparql_filter: {
              type: "string",
              description: "Optional SPARQL WHERE clause to filter results"
            },
            top_k: {
              type: "number",
              description: "Number of results",
              default: 10
            }
          },
          required: ["query"]
        }
      },
      {
        name: "query_entities",
        description: "Query entities from the knowledge graph",
        inputSchema: {
          type: "object",
          properties: {
            entity_type: {
              type: "string",
              description: "Type of entity (e.g., Agent, System, MetabolicFlow)"
            },
            limit: {
              type: "number",
              description: "Maximum number of results",
              default: 20
            }
          }
        }
      },
      {
        name: "explore_graph",
        description: "Explore relationships for a specific entity",
        inputSchema: {
          type: "object",
          properties: {
            entity_uri: {
              type: "string",
              description: "URI of the entity to explore"
            },
            depth: {
              type: "number",
              description: "Depth of exploration",
              default: 2
            },
            limit: {
              type: "number",
              description: "Maximum relationships to return",
              default: 50
            }
          },
          required: ["entity_uri"]
        }
      },
      {
        name: "knowledge_stats",
        description: "Get statistics about the KOI knowledge base",
        inputSchema: {
          type: "object",
          properties: {}
        }
      },
      {
        name: "nl_query",
        description: "Convert natural language question to SPARQL and execute",
        inputSchema: {
          type: "object",
          properties: {
            question: {
              type: "string",
              description: "Natural language question about the knowledge"
            }
          },
          required: ["question"]
        }
      }
    ]
  }));

  // Register tool call handler
  server.setRequestHandler(CallToolRequestSchema, async (request) => {
    const { name, arguments: args } = request.params;

    try {
      let result: any;

      switch (name) {
        case "bge_search":
          result = await searchEmbeddings(
            args.query as string,
            args.top_k as number || 10
          );
          break;

        case "sparql_query":
          result = await executeSparqlQuery(args.query as string);
          break;

        case "hybrid_search":
          result = await hybridSearch(
            args.query as string,
            args.sparql_filter as string,
            args.top_k as number || 10
          );
          break;

        case "query_entities":
          result = await queryEntities(
            args.entity_type as string,
            args.limit as number || 20
          );
          break;

        case "explore_graph":
          result = await exploreGraph(
            args.entity_uri as string,
            args.depth as number || 2,
            args.limit as number || 50
          );
          break;

        case "knowledge_stats":
          result = await getKnowledgeStats();
          break;

        case "nl_query":
          // Use default SPARQL generation based on keywords
          const nlQuery = generateDefaultSparqlFromNL(args.question as string);
          result = {
            question: args.question,
            generated_sparql: nlQuery,
            result: await executeSparqlQuery(nlQuery)
          };
          break;

        default:
          throw new Error(`Unknown tool: ${name}`);
      }

      return {
        content: [
          {
            type: "text",
            text: JSON.stringify(result, null, 2)
          }
        ]
      };
    } catch (error: any) {
      return {
        content: [
          {
            type: "text",
            text: JSON.stringify({ error: error.message }, null, 2)
          }
        ]
      };
    }
  });

  // Start server with stdio transport
  const transport = new StdioServerTransport();
  await server.connect(transport);

  console.error("[KOI-MCP] Enhanced Knowledge Server ready (vector + graph)");
}

// Run the server
main().catch((error) => {
  console.error("[KOI-MCP] Fatal error:", error);
  process.exit(1);
});