#!/usr/bin/env python3
"""
Pipeline Metadata API
Provides access to KOI pipeline structure and component metadata
"""

import os
import json
import asyncio
import httpx
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import logging
from datetime import datetime
from rdflib import Graph, Namespace, URIRef, Literal, RDF, RDFS

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# FastAPI app
app = FastAPI(title="Pipeline Metadata API", version="1.0.0")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Define namespaces
KOI = Namespace("http://koi.regen.network/ontology#")
INFRA = Namespace("http://koi.regen.network/infrastructure/")
SENSOR = Namespace("http://koi.regen.network/sensor/")

# Pydantic models
class Component(BaseModel):
    id: str
    rid: str
    type: str  # sensor, processor, storage, service
    label: str
    status: str
    endpoint: Optional[str] = None
    port: Optional[int] = None
    description: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = {}

class Connection(BaseModel):
    source: str
    target: str
    type: str  # data, control, monitoring, query
    label: Optional[str] = None  # Label for the edge based on predicate

class PipelineStructure(BaseModel):
    components: List[Component]
    connections: List[Connection]
    metadata: Dict[str, Any]

class ComponentStatus(BaseModel):
    id: str
    status: str
    last_updated: str
    metrics: Optional[Dict[str, Any]] = {}

# Load and parse RDF data
def load_pipeline_metadata() -> Graph:
    """Load pipeline metadata from TTL files"""
    g = Graph()

    # Bind namespaces
    g.bind("koi", KOI)
    g.bind("", INFRA)
    g.bind("sensor", SENSOR)
    g.bind("rdfs", RDFS)

    # Load ontology
    ontology_path = "/opt/projects/koi-processor/koi-ontology.ttl"
    if os.path.exists(ontology_path):
        g.parse(ontology_path, format="turtle")
        logger.info(f"Loaded ontology from {ontology_path}")

    # Load pipeline metadata
    metadata_path = "/opt/projects/koi-processor/pipeline-metadata.ttl"
    if os.path.exists(metadata_path):
        g.parse(metadata_path, format="turtle")
        logger.info(f"Loaded pipeline metadata from {metadata_path}")

    return g

def extract_components(graph: Graph) -> List[Component]:
    """Extract components from RDF graph"""
    components = []

    # Query for all components (sensors, processors, storage, services)
    query = """
    PREFIX koi: <http://koi.regen.network/ontology#>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    PREFIX dc: <http://purl.org/dc/elements/1.1/>

    SELECT ?component ?type ?label ?rid ?status ?endpoint ?port ?description
    WHERE {
        ?component a ?class .
        ?class rdfs:subClassOf* koi:Component .
        OPTIONAL { ?component rdfs:label ?label }
        OPTIONAL { ?component koi:rid ?rid }
        OPTIONAL { ?component koi:status ?status }
        OPTIONAL { ?component koi:endpoint ?endpoint }
        OPTIONAL { ?component koi:port ?port }
        OPTIONAL { ?component dc:description ?description }
        OPTIONAL { ?component koi:sensorType ?sensorType }
        BIND(
            IF(EXISTS { ?component a koi:Sensor }, "sensor",
            IF(EXISTS { ?component a koi:Processor }, "processor",
            IF(EXISTS { ?component a koi:Coordinator }, "processor",
            IF(EXISTS { ?component a koi:Storage }, "storage",
            IF(EXISTS { ?component a koi:Service }, "service", "unknown")))))
            AS ?type
        )
    }
    """

    results = graph.query(query)

    for row in results:
        # Extract component ID from URI
        component_uri = str(row.component)
        component_id = component_uri.split("/")[-1].split("#")[-1]

        components.append(Component(
            id=component_id,
            rid=str(row.rid) if row.rid else f"koi.infrastructure:{component_id}",
            type=str(row.type) if row.type else "unknown",
            label=str(row.label) if row.label else component_id,
            status=str(row.status) if row.status else "unknown",
            endpoint=str(row.endpoint) if row.endpoint else None,
            port=int(row.port) if row.port else None,
            description=str(row.description) if row.description else None
        ))

    return components

def extract_connections(graph: Graph) -> List[Connection]:
    """Extract connections from RDF graph"""
    connections = []

    # Query for all connections
    query = """
    PREFIX koi: <http://koi.regen.network/ontology#>

    SELECT ?source ?target ?predicate
    WHERE {
        ?source ?predicate ?target .
        FILTER(?predicate IN (koi:sendsTo, koi:receivesFrom, koi:forwardsTo, koi:queriedBy, koi:queriesFrom, koi:writesTo))
    }
    """

    results = graph.query(query)

    for row in results:
        # Extract IDs from URIs
        source_id = str(row.source).split("/")[-1].split("#")[-1]
        target_id = str(row.target).split("/")[-1].split("#")[-1]
        predicate = str(row.predicate).split("#")[-1]

        # Map predicates to connection types and labels
        connection_type = "data"
        label = predicate.replace("_", " ").title()  # Default label

        if predicate == "queriedBy":
            connection_type = "query"
            label = "queries"
        elif predicate == "queriesFrom":
            connection_type = "query"
            label = "queries"
        elif predicate == "monitors":
            connection_type = "monitoring"
            label = "monitors"
        elif predicate == "sendsTo":
            label = "sends to"
        elif predicate == "receivesFrom":
            label = "receives"
        elif predicate == "forwardsTo":
            label = "forwards"
        elif predicate == "writesTo":
            label = "writes"
            connection_type = "data"

        connections.append(Connection(
            source=source_id,
            target=target_id,
            type=connection_type,
            label=label
        ))

    return connections

async def get_live_sensor_status() -> Dict[str, Any]:
    """Fetch live sensor status from coordinator"""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get("http://localhost:8005/sensors")
            if response.status_code == 200:
                return response.json()
    except Exception as e:
        logger.warning(f"Could not fetch live sensor status: {e}")

    return {"sensors": []}

async def get_service_health(endpoint: str) -> str:
    """Check health of a service endpoint"""
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.get(f"{endpoint}/health")
            if response.status_code == 200:
                return "active"
    except:
        pass
    return "offline"

# API Endpoints

@app.get("/health")
async def health():
    """Health check endpoint"""
    return {"status": "healthy", "service": "Pipeline Metadata API"}

@app.get("/api/koi/graph/pipeline", response_model=PipelineStructure)
async def get_pipeline_structure():
    """Get complete pipeline structure with components and connections"""

    # Load RDF data
    graph = load_pipeline_metadata()

    # Extract components and connections
    components = extract_components(graph)
    connections = extract_connections(graph)

    # Get live sensor status
    sensor_data = await get_live_sensor_status()

    # Update component status with live data
    sensor_map = {s["id"]: s for s in sensor_data.get("sensors", [])}

    for component in components:
        if component.type == "sensor" and component.id in sensor_map:
            sensor_info = sensor_map[component.id]
            component.status = sensor_info.get("status", "unknown")
            component.metadata = {
                "lastActivity": sensor_info.get("lastActivity"),
                "eventsProcessed": sensor_info.get("eventsProcessed"),
                "monitoring": sensor_info.get("monitoring", [])
            }

    return PipelineStructure(
        components=components,
        connections=connections,
        metadata={
            "timestamp": datetime.utcnow().isoformat(),
            "version": "2.0.0",
            "graph_size": len(graph)
        }
    )

@app.get("/api/koi/graph/components")
async def get_components(component_type: Optional[str] = None):
    """Get all components, optionally filtered by type"""

    graph = load_pipeline_metadata()
    components = extract_components(graph)

    if component_type:
        components = [c for c in components if c.type == component_type]

    return {"components": components, "count": len(components)}

@app.get("/api/koi/graph/connections")
async def get_connections(component_id: Optional[str] = None):
    """Get all connections, optionally filtered by component"""

    graph = load_pipeline_metadata()
    connections = extract_connections(graph)

    if component_id:
        connections = [
            c for c in connections
            if c.source == component_id or c.target == component_id
        ]

    return {"connections": connections, "count": len(connections)}

@app.get("/api/koi/graph/component/{component_id}")
async def get_component_detail(component_id: str):
    """Get detailed information about a specific component"""

    graph = load_pipeline_metadata()
    components = extract_components(graph)

    # Find the component
    component = next((c for c in components if c.id == component_id), None)

    if not component:
        raise HTTPException(status_code=404, detail="Component not found")

    # Get connections for this component
    connections = extract_connections(graph)
    incoming = [c for c in connections if c.target == component_id]
    outgoing = [c for c in connections if c.source == component_id]

    # Check live status if it's a service
    if component.endpoint:
        component.status = await get_service_health(component.endpoint)

    return {
        "component": component,
        "incoming_connections": incoming,
        "outgoing_connections": outgoing
    }

@app.get("/api/koi/graph/provenance/{rid:path}")
async def get_document_provenance(rid: str):
    """Get provenance information for a document through the pipeline"""

    import asyncpg
    from urllib.parse import unquote

    # Decode the RID if it was URL encoded
    rid = unquote(rid)

    try:
        # Connect to database
        conn = await asyncpg.connect(
            host='localhost',
            port=5433,
            database='eliza',
            user='postgres',
            password='postgres'
        )

        try:
            # Query transformation receipts for this RID
            receipts = await conn.fetch("""
                WITH RECURSIVE chain AS (
                    -- Find all transformations involving this RID
                    SELECT DISTINCT * FROM (
                        SELECT * FROM koi_transformation_receipts
                        WHERE input_rid = $1 OR output_rid = $1

                        UNION

                        -- Find upstream transformations
                        SELECT t.* FROM koi_transformation_receipts t
                        WHERE t.output_rid IN (
                            SELECT input_rid FROM koi_transformation_receipts
                            WHERE output_rid = $1
                        )
                    ) AS all_receipts
                )
                SELECT
                    receipt_id,
                    transformation_type,
                    input_rid,
                    output_rid,
                    source_sensor,
                    event_type,
                    chunks_created,
                    embeddings_created,
                    entities_extracted,
                    processor_name,
                    created_at,
                    metadata
                FROM chain
                ORDER BY created_at ASC
            """, rid)

            # Build provenance timeline
            timeline = []
            sensors = set()
            processors = set()
            storage = set()

            for receipt in receipts:
                # Add to timeline
                timeline.append({
                    "timestamp": receipt['created_at'].isoformat() if receipt['created_at'] else None,
                    "type": receipt['transformation_type'],
                    "receipt_id": receipt['receipt_id'],
                    "input_rid": receipt['input_rid'],
                    "output_rid": receipt['output_rid'],
                    "details": {
                        "processor": receipt['processor_name'],
                        "chunks_created": receipt['chunks_created'],
                        "embeddings_created": receipt['embeddings_created'],
                        "entities_extracted": receipt['entities_extracted']
                    }
                })

                # Track components
                if receipt['source_sensor']:
                    sensors.add(receipt['source_sensor'])
                if receipt['processor_name']:
                    processors.add(receipt['processor_name'])

                # Infer storage from transformation types
                if 'embedding' in receipt['transformation_type']:
                    storage.add('postgresql-pgvector')
                if 'graph' in receipt['transformation_type']:
                    storage.add('apache-jena')
                if 'memory' in receipt['transformation_type']:
                    storage.add('postgresql')

            # Also check if document exists in koi_content
            doc = await conn.fetchrow("""
                SELECT rid as id, title, metadata->>'source' as source_sensor, created_at, content_hash
                FROM koi_content
                WHERE rid = $1
            """, rid)

            if doc:
                if doc['source_sensor']:
                    sensors.add(doc['source_sensor'])

            return {
                "rid": rid,
                "found": len(receipts) > 0 or doc is not None,
                "document": {
                    "title": doc['title'] if doc else None,
                    "source_sensor": doc['source_sensor'] if doc else None,
                    "created_at": doc['created_at'].isoformat() if doc and doc['created_at'] else None,
                    "content_hash": doc['content_hash'] if doc else None
                } if doc else None,
                "provenance": {
                    "sensed_by": list(sensors),
                    "processed_by": list(processors),
                    "stored_in": list(storage),
                    "transformation_count": len(receipts),
                    "first_seen": timeline[0]["timestamp"] if timeline else None,
                    "last_updated": timeline[-1]["timestamp"] if timeline else None
                },
                "timeline": timeline
            }

        finally:
            await conn.close()

    except Exception as e:
        logger.error(f"Error fetching provenance for {rid}: {e}")
        return {
            "rid": rid,
            "error": str(e),
            "found": False
        }

@app.get("/api/koi/rids")
async def list_available_rids(limit: int = Query(default=50, le=500)):
    """List available RIDs from the system for testing/browsing"""

    import asyncpg

    try:
        # Connect to database
        conn = await asyncpg.connect(
            host='localhost',
            port=5433,
            database='eliza',
            user='postgres',
            password='postgres'
        )

        try:
            # Get RIDs from documents
            doc_rids = await conn.fetch("""
                SELECT DISTINCT
                    rid,
                    title,
                    metadata->>'source' as source_sensor,
                    created_at
                FROM koi_content
                WHERE rid IS NOT NULL
                ORDER BY created_at DESC
                LIMIT $1
            """, limit // 2)

            # Get RIDs from transformation receipts
            receipt_rids = await conn.fetch("""
                SELECT DISTINCT rid, created_at FROM (
                    SELECT DISTINCT input_rid as rid, MIN(created_at) as created_at
                    FROM koi_transformation_receipts
                    WHERE input_rid IS NOT NULL
                    GROUP BY input_rid

                    UNION

                    SELECT DISTINCT output_rid as rid, MIN(created_at) as created_at
                    FROM koi_transformation_receipts
                    WHERE output_rid IS NOT NULL
                    GROUP BY output_rid
                ) AS all_rids
                ORDER BY created_at DESC
                LIMIT $1
            """, limit // 2)

            # Combine and format results
            rids = []
            seen = set()

            for doc in doc_rids:
                if doc['rid'] not in seen:
                    rids.append({
                        "rid": doc['rid'],
                        "title": doc['title'],
                        "source": doc['source_sensor'],
                        "type": "document",
                        "created_at": doc['created_at'].isoformat() if doc['created_at'] else None
                    })
                    seen.add(doc['rid'])

            for receipt in receipt_rids:
                if receipt['rid'] not in seen and len(rids) < limit:
                    rids.append({
                        "rid": receipt['rid'],
                        "title": None,
                        "source": "transformation",
                        "type": "receipt",
                        "created_at": receipt['created_at'].isoformat() if receipt['created_at'] else None
                    })
                    seen.add(receipt['rid'])

            return {
                "rids": rids,
                "count": len(rids),
                "total_available": await conn.fetchval("""
                    SELECT COUNT(DISTINCT rid) FROM (
                        SELECT rid FROM koi_content WHERE rid IS NOT NULL
                        UNION
                        SELECT input_rid as rid FROM koi_transformation_receipts WHERE input_rid IS NOT NULL
                        UNION
                        SELECT output_rid as rid FROM koi_transformation_receipts WHERE output_rid IS NOT NULL
                    ) AS all_rids
                """)
            }

        finally:
            await conn.close()

    except Exception as e:
        logger.error(f"Error listing RIDs: {e}")
        return {
            "error": str(e),
            "rids": []
        }

@app.get("/api/koi/cat/chain/{rid:path}")
async def get_cat_receipt_chain(rid: str):
    """Get complete CAT receipt chain for a RID using the CATReceiptChain class"""

    from urllib.parse import unquote
    import sys
    import os

    # Add parent directory to path to import CAT module
    sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'src', 'cat'))

    try:
        from cat_receipt_chain import CATReceiptChain
    except ImportError:
        logger.error("Could not import CATReceiptChain")
        return {
            "rid": rid,
            "error": "CAT Receipt Chain module not available",
            "found": False
        }

    # Decode the RID if it was URL encoded
    rid = unquote(rid)

    try:
        # Initialize CAT receipt chain
        chain = CATReceiptChain()
        await chain.initialize()

        # Get provenance report
        report = await chain.get_provenance_report(rid)

        # Verify the chain
        verification = await chain.verify_chain(rid)

        # Get the complete chain
        receipts = await chain.get_chain(rid)

        # Format the response
        formatted_chain = []
        for receipt in receipts:
            formatted_chain.append({
                "rid": receipt.rid,
                "type": receipt.type,
                "timestamp": receipt.timestamp,
                "parent_rid": receipt.parent_rid,
                "content_cid": receipt.content_cid,
                "transformation": receipt.transformation,
                "metadata": receipt.metadata,
                "hash": receipt.hash,
                "hash_valid": True  # Will be updated from verification
            })

        # Add verification status to each receipt
        for i, receipt_info in enumerate(formatted_chain):
            if i < len(verification.get("receipts", [])):
                receipt_info["hash_valid"] = verification["receipts"][i].get("hash_valid", True)
                receipt_info["parent_valid"] = verification["receipts"][i].get("parent_valid", True)

        return {
            "rid": rid,
            "found": len(receipts) > 0,
            "chain": {
                "length": len(receipts),
                "valid": verification.get("valid", False),
                "receipts": formatted_chain,
                "errors": verification.get("errors", [])
            },
            "report": report,
            "verification": verification
        }

    except Exception as e:
        logger.error(f"Error fetching CAT chain for {rid}: {e}")
        return {
            "rid": rid,
            "error": str(e),
            "found": False
        }

@app.get("/api/koi/graph/status")
async def get_pipeline_status():
    """Get current status of all pipeline components"""

    graph = load_pipeline_metadata()
    components = extract_components(graph)

    # Check status of each component
    status_list = []

    for component in components:
        status = ComponentStatus(
            id=component.id,
            status=component.status,
            last_updated=datetime.utcnow().isoformat()
        )

        # Add specific metrics based on component type
        if component.type == "sensor":
            sensor_data = await get_live_sensor_status()
            sensor_info = next(
                (s for s in sensor_data.get("sensors", []) if s["id"] == component.id),
                None
            )
            if sensor_info:
                status.metrics = {
                    "events_processed": sensor_info.get("eventsProcessed", 0),
                    "last_activity": sensor_info.get("lastActivity")
                }

        status_list.append(status)

    return {
        "components": status_list,
        "overall_status": "operational",
        "timestamp": datetime.utcnow().isoformat()
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002)