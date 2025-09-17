#!/usr/bin/env python3
"""
KOI Event Bridge with Semantic Extraction
Extends v2 with LLM extraction, knowledge graph, and CAT receipt chain
"""

import os
import sys
import json
import asyncio
import asyncpg
import httpx
import hashlib
import logging
import uuid
import time
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Add parent directory to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import semantic extraction components
# from extraction.llm_extractor import OntologyLLMExtractor  # Ollama version
from extraction.openai_extractor import OpenAIExtractor  # OpenAI GPT-4o-mini version
from extraction.metadata_resolver import MetadataResolver
from extraction.smart_chunker import SmartChunker
from knowledge_graph.graph_integration import KnowledgeGraphIntegrator
from cat.cat_receipt_chain import CATReceiptChain

# Import from existing event bridge
from core.koi_event_bridge_v2 import (
    KOIManifest, KOIBundle, KOIEvent, ProcessingResult,
    generate_embedding_bge, check_existing_memory
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# FastAPI app
app = FastAPI(title="KOI Event Bridge Semantic", version="3.0.0")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuration
DB_URL = os.getenv('POSTGRES_URL', 'postgresql://postgres:postgres@localhost:5433/eliza')
BGE_API_URL = os.getenv('BGE_API_URL', 'http://localhost:8090/encode')
USE_ISOLATED_TABLES = os.getenv('USE_ISOLATED_TABLES', 'true').lower() == 'true'
ENABLE_LLM_EXTRACTION = os.getenv('ENABLE_LLM_EXTRACTION', 'true').lower() == 'true'
OLLAMA_URL = os.getenv('OLLAMA_URL', 'http://localhost:11434')

# Initialize semantic extraction components
# Use OpenAI GPT-4o-mini for extraction instead of Ollama
llm_extractor = OpenAIExtractor(
    model="gpt-4o-mini",
    use_batch_api=False  # Set to True for batch processing
)

metadata_resolver = MetadataResolver()

smart_chunker = SmartChunker()

kg_integrator = KnowledgeGraphIntegrator(
    store_type="memory",  # Use memory for now, can switch to postgresql
    store_config={}
)

cat_chain = CATReceiptChain(
    db_config={
        "host": "localhost",
        "port": 5433,
        "database": "eliza",
        "user": "postgres",
        "password": "postgres"
    }
)

# Initialize CAT chain on startup
@app.on_event("startup")
async def startup_event():
    """Initialize CAT receipt chain on startup"""
    await cat_chain.initialize()
    logger.info("Semantic Event Bridge initialized with CAT receipt chain")

def get_source_type(source_node: str) -> str:
    """Map KOI source to ontology source type"""
    source_mapping = {
        'discourse': 'discourse',
        'twitter': 'twitter',
        'medium': 'medium',
        'github': 'github',
        'telegram': 'telegram',
        'website': 'website',
        'notion': 'website',
        'gitlab': 'github',
        'podcast': 'medium'
    }
    
    source_lower = source_node.lower()
    for key, value in source_mapping.items():
        if key in source_lower:
            return value
    
    return 'website'  # Default to website ontology

async def extract_and_chunk_content(
    event: KOIEvent,
    text_content: str,
    metadata: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Extract semantic metadata and chunk content with CAT receipts
    
    Returns:
        Dictionary containing chunks, metadata, and CAT receipt RID
    """
    result = {
        "chunks": [],
        "metadata": metadata,
        "cat_receipt_rid": None,
        "extraction_report": None,
        "kg_report": None
    }
    
    try:
        # Create initial CAT receipt for sensor collection
        sensor_receipt = await cat_chain.create_sensor_receipt(
            sensor_name=event.source_node,
            source_url=metadata.get('url', 'unknown'),
            content_cid=event.bundle.rid,
            document_count=1,
            metadata=metadata
        )
        result["cat_receipt_rid"] = sensor_receipt.rid
        
        # Apply LLM extraction if enabled
        enhanced_metadata = metadata
        extraction_receipt = None
        resolved_metadata = metadata  # Default to sensor metadata

        if ENABLE_LLM_EXTRACTION:
            try:
                # Determine source type
                source_type = get_source_type(event.source_node)

                # Extract semantic metadata with confidence scores
                llm_extraction = await llm_extractor.extract_metadata(
                    content=text_content,
                    source_type=source_type,
                    existing_metadata=metadata
                )

                # Resolve metadata conflicts between sensor and LLM
                if llm_extraction.get('llm_extracted_metadata'):
                    resolved_metadata = metadata_resolver.resolve_metadata(
                        sensor_metadata=metadata,
                        llm_metadata=llm_extraction.get('llm_extracted_metadata', {}),
                        llm_confidence=llm_extraction.get('llm_metadata_confidence', {})
                    )
                    # Merge resolved metadata with extraction results
                    enhanced_metadata = {**llm_extraction, **resolved_metadata}
                else:
                    enhanced_metadata = llm_extraction
                
                # Create extraction CAT receipt
                entities_count = len(enhanced_metadata.get('extracted_entities', []))
                relationships_count = len(enhanced_metadata.get('extracted_relationships', []))
                
                extraction_receipt = await cat_chain.create_extraction_receipt(
                    parent_rid=sensor_receipt.rid,
                    content_cid=f"{event.bundle.rid}:extracted",
                    model=llm_extractor.model,
                    ontology=source_type,
                    entities_count=entities_count,
                    relationships_count=relationships_count
                )
                
                result["extraction_report"] = {
                    "entities": entities_count,
                    "relationships": relationships_count,
                    "source_type": source_type
                }
                
                logger.info(f"LLM extraction: {entities_count} entities, {relationships_count} relationships")
                
            except Exception as e:
                logger.warning(f"LLM extraction failed: {e}")
                enhanced_metadata = metadata
        
        # Update result metadata
        result["metadata"] = enhanced_metadata
        
        # Integrate into knowledge graph if extraction succeeded
        graph_receipt = None
        if enhanced_metadata.get('extracted_entities') or enhanced_metadata.get('extracted_relationships'):
            try:
                # Create document for graph integration
                document = {
                    'rid': event.bundle.rid,
                    'url': metadata.get('url', ''),
                    'title': enhanced_metadata.get('title', ''),
                    'content': text_content,
                    'source_type': get_source_type(event.source_node),
                    'metadata': enhanced_metadata
                }
                
                # Integrate into knowledge graph
                kg_report = kg_integrator.integrate_document(
                    document=document,
                    extraction_metadata=enhanced_metadata
                )
                
                # Create graph integration receipt
                parent_rid = extraction_receipt.rid if extraction_receipt else sensor_receipt.rid
                graph_receipt = await cat_chain.create_graph_receipt(
                    parent_rid=parent_rid,
                    content_cid=f"{event.bundle.rid}:graph",
                    triples_added=kg_report.get('triples_added', 0),
                    store_type=kg_integrator.store_type
                )
                
                result["kg_report"] = kg_report
                result["cat_receipt_rid"] = graph_receipt.rid
                
                logger.info(f"Graph integration: {kg_report.get('triples_added', 0)} triples added")
                
            except Exception as e:
                logger.warning(f"Knowledge graph integration failed: {e}")
        
        # Use intelligent chunking based on source type and extracted entities
        chunk_results = smart_chunker.chunk_content(
            content=text_content,
            source_type=get_source_type(event.source_node),
            extracted_entities=enhanced_metadata.get('extracted_entities', []),
            metadata=resolved_metadata
        )

        # Extract just the text from chunk results
        chunks = [chunk['text'] for chunk in chunk_results]
        
        # Create chunking receipt
        parent_rid = graph_receipt.rid if graph_receipt else (
            extraction_receipt.rid if extraction_receipt else sensor_receipt.rid
        )
        
        chunk_receipt = await cat_chain.create_chunking_receipt(
            parent_rid=parent_rid,
            content_cid=f"{event.bundle.rid}:chunks",
            chunk_count=len(chunks),
            chunk_size=1000,
            overlap=200
        )
        
        result["chunks"] = chunks
        result["cat_receipt_rid"] = chunk_receipt.rid
        
    except Exception as e:
        logger.error(f"Error in semantic extraction pipeline: {e}")
        # Fall back to basic chunking
        result["chunks"] = chunk_text(text_content, chunk_size=1000, overlap=200)
    
    return result

def chunk_text(text: str, chunk_size: int = 1000, overlap: int = 200) -> List[str]:
    """Chunk text into smaller pieces with overlap"""
    if not text:
        return []
    
    chunks = []
    start = 0
    
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end]
        
        # Try to end at a sentence boundary
        if end < len(text):
            last_period = chunk.rfind('.')
            last_newline = chunk.rfind('\n')
            break_point = max(last_period, last_newline)
            
            if break_point > chunk_size * 0.5:
                chunk = chunk[:break_point + 1]
                end = start + break_point + 1
        
        chunks.append(chunk.strip())
        start = end - overlap
    
    return chunks

async def process_koi_event_semantic(event: KOIEvent) -> ProcessingResult:
    """Process a KOI event with semantic extraction"""
    start_time = time.time()

    # Validate event has bundle
    if not event.bundle:
        logger.error(f"Event missing bundle: {event}")
        return ProcessingResult(
            success=False,
            rid=event.rid if hasattr(event, 'rid') else "",
            cid="",
            chunks_created=0,
            embeddings_created=0,
            error="Event missing bundle"
        )

    try:
        async with asyncpg.create_pool(DB_URL) as pool:
            async with pool.acquire() as conn:
                # Check for existing memory
                existing = await check_existing_memory(conn, event.bundle.rid)
                
                # Handle FORGET events
                if event.event_type == "FORGET":
                    if existing and USE_ISOLATED_TABLES:
                        await conn.execute("""
                            UPDATE koi_memories 
                            SET superseded_at = $1 
                            WHERE rid = $2 AND superseded_at IS NULL
                        """, datetime.now(tz=timezone.utc), event.bundle.rid)
                    
                    return ProcessingResult(
                        success=True,
                        rid=event.bundle.rid,
                        cid="",
                        chunks_created=0,
                        embeddings_created=0
                    )
                
                # Skip if NEW and already exists
                if event.event_type == "NEW" and existing:
                    logger.info(f"Content {event.bundle.rid} already exists, skipping")
                    return ProcessingResult(
                        success=False,
                        rid=event.bundle.rid,
                        cid="",
                        chunks_created=0,
                        embeddings_created=0,
                        error="Content already exists"
                    )
                
                # Extract text content
                text_content = extract_text_from_bundle(event.bundle)
                if not text_content:
                    return ProcessingResult(
                        success=False,
                        rid=event.bundle.rid,
                        cid="",
                        chunks_created=0,
                        embeddings_created=0,
                        error="No text content found"
                    )
                
                # Apply semantic extraction and chunking
                extraction_result = await extract_and_chunk_content(
                    event,
                    text_content,
                    event.bundle.manifest.metadata or {}
                )
                
                chunks = extraction_result["chunks"]
                enhanced_metadata = extraction_result["metadata"]
                cat_receipt_rid = extraction_result["cat_receipt_rid"]
                
                # Store chunks with enhanced metadata
                chunks_created = 0
                embeddings_created = 0
                
                for i, chunk in enumerate(chunks):
                    # Generate unique ID for chunk
                    chunk_id = str(uuid.uuid4())
                    
                    # Store chunk memory with enhanced metadata
                    chunk_metadata = {
                        **enhanced_metadata,
                        "chunk_index": i,
                        "total_chunks": len(chunks),
                        "cat_receipt_rid": cat_receipt_rid,
                        "extraction_report": extraction_result.get("extraction_report"),
                        "kg_report": extraction_result.get("kg_report")
                    }
                    
                    await store_memory_chunk(
                        conn,
                        chunk_id,
                        event.bundle.rid,
                        f"chunk_{i}",
                        chunk,
                        chunk_metadata,
                        event
                    )
                    chunks_created += 1
                    
                    # Generate embedding
                    embedding = await generate_embedding_bge(chunk)
                    if embedding:
                        await store_embedding(conn, chunk_id, embedding)
                        
                        # Create embedding CAT receipt
                        await cat_chain.create_embedding_receipt(
                            parent_rid=cat_receipt_rid,
                            content_cid=f"{event.bundle.rid}:chunk_{i}:embedding",
                            model="bge-m3",
                            dimension=len(embedding)
                        )
                        embeddings_created += 1
                
                # Calculate CID
                cid = f"cid:sha256:{event.bundle.manifest.content_hash}"
                
                elapsed = time.time() - start_time
                logger.info(f"Processed {event.bundle.rid} with semantic extraction in {elapsed:.2f}s")
                
                return ProcessingResult(
                    success=True,
                    rid=event.bundle.rid,
                    cid=cid,
                    chunks_created=chunks_created,
                    embeddings_created=embeddings_created,
                    version=existing['version'] + 1 if existing else 1
                )
                
    except Exception as e:
        logger.error(f"Error processing event: {e}")
        return ProcessingResult(
            success=False,
            rid=event.bundle.rid if event.bundle else "",
            cid="",
            chunks_created=0,
            embeddings_created=0,
            error=str(e)
        )

def extract_text_from_bundle(bundle: KOIBundle) -> str:
    """Extract text content from KOI bundle"""
    text_parts = []
    
    # Handle different content structures
    if isinstance(bundle.contents, dict):
        # Look for text fields
        if 'text' in bundle.contents:
            text_parts.append(str(bundle.contents['text']))
        if 'content' in bundle.contents:
            text_parts.append(str(bundle.contents['content']))
        if 'document' in bundle.contents:
            doc = bundle.contents['document']
            if isinstance(doc, dict):
                if 'content' in doc:
                    text_parts.append(str(doc['content']))
                if 'text' in doc:
                    text_parts.append(str(doc['text']))
        
        # Look for title/description
        if 'title' in bundle.contents:
            text_parts.append(f"Title: {bundle.contents['title']}")
        if 'description' in bundle.contents:
            text_parts.append(f"Description: {bundle.contents['description']}")
    
    elif isinstance(bundle.contents, str):
        text_parts.append(bundle.contents)
    
    elif isinstance(bundle.contents, list):
        for item in bundle.contents:
            if isinstance(item, str):
                text_parts.append(item)
            elif isinstance(item, dict) and 'text' in item:
                text_parts.append(str(item['text']))
    
    return '\n\n'.join(text_parts)

async def store_memory_chunk(
    conn: asyncpg.Connection,
    memory_id: str,
    rid: str,
    chunk_id: str,
    content: str,
    metadata: Dict[str, Any],
    event: KOIEvent
) -> None:
    """Store a memory chunk with metadata"""
    if USE_ISOLATED_TABLES:
        # Extract publication date
        published_at = None
        published_confidence = 0.0
        
        if 'published_at' in metadata:
            date_str = metadata['published_at']
            if isinstance(date_str, str):
                try:
                    published_at = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                    published_confidence = metadata.get('published_confidence', 0.9)
                except:
                    pass
        
        # Calculate content hash
        content_hash = hashlib.sha256(content.encode('utf-8')).hexdigest()
        
        # Store in koi_memories
        await conn.execute("""
            INSERT INTO koi_memories (
                id, rid, cid, version, event_type, source_sensor,
                content, metadata, published_at, published_confidence,
                content_hash
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            ON CONFLICT (id) DO NOTHING
        """,
            memory_id,
            f"{rid}:{chunk_id}",
            f"{rid}:{chunk_id}",
            1,
            event.event_type,
            event.source_node,
            json.dumps({"text": content}),
            json.dumps(metadata),
            published_at,
            published_confidence,
            content_hash
        )
    else:
        # Store in legacy memories table
        agent_id = "8e1e4498-b3c8-0fae-ad1f-e90d1c1a4331"
        await conn.execute("""
            INSERT INTO memories (id, type, content, "agentId", "createdAt")
            VALUES ($1::uuid, 'koi_chunk', $2::jsonb, $3::uuid, CURRENT_TIMESTAMP)
            ON CONFLICT (id) DO NOTHING
        """,
            memory_id,
            json.dumps({
                "text": content,
                "rid": f"{rid}:{chunk_id}",
                "metadata": metadata
            }),
            agent_id
        )

async def store_embedding(conn: asyncpg.Connection, memory_id: str, embedding: List[float]) -> bool:
    """Store embedding in database"""
    if not embedding:
        return False
    
    embedding_str = '[' + ','.join(map(str, embedding)) + ']'
    embedding_dim = len(embedding)
    
    try:
        if USE_ISOLATED_TABLES:
            if embedding_dim == 768:
                await conn.execute("""
                    INSERT INTO koi_embeddings (memory_id, dim_768)
                    VALUES ($1, $2::vector(768))
                    ON CONFLICT (memory_id) DO UPDATE SET dim_768 = $2::vector(768)
                """, memory_id, embedding_str)
            elif embedding_dim == 1024:
                await conn.execute("""
                    INSERT INTO koi_embeddings (memory_id, dim_1024)
                    VALUES ($1, $2::vector(1024))
                    ON CONFLICT (memory_id) DO UPDATE SET dim_1024 = $2::vector(1024)
                """, memory_id, embedding_str)
            else:
                logger.warning(f"Unsupported embedding dimension: {embedding_dim}")
                return False
        return True
    except Exception as e:
        logger.error(f"Error storing embedding: {e}")
        return False

# API Endpoints
@app.post("/events/process", response_model=ProcessingResult)
async def process_event(event: KOIEvent):
    """Process a single KOI event with semantic extraction"""

    # Log the received event for debugging
    logger.info(f"Received event: {event}")
    logger.info(f"Event type: {event.event_type}")
    logger.info(f"Event has bundle: {event.bundle is not None}")

    # If event doesn't have a bundle but has data, create one
    if not event.bundle and hasattr(event, 'data') and event.data:
        from core.koi_event_bridge_v2 import KOIBundle, KOIManifest

        # Create bundle from data
        event.bundle = KOIBundle(
            rid=event.rid or event.data.get('rid', ''),
            cid=event.data.get('cid', ''),
            manifest=KOIManifest(
                title=event.data.get('title', ''),
                description=event.data.get('description', ''),
                content=event.data.get('content', ''),
                url=event.data.get('url', ''),
                metadata=event.data
            )
        )
        logger.info(f"Created bundle from event data with RID: {event.bundle.rid}")

    return await process_koi_event_semantic(event)

@app.post("/events/batch")
async def process_batch(events: List[KOIEvent]):
    """Process a batch of KOI events"""
    results = []
    for event in events:
        result = await process_koi_event_semantic(event)
        results.append(result.dict())
    return {"processed": len(results), "results": results}

@app.get("/cat/receipt/{rid}")
async def get_cat_receipt(rid: str):
    """Get a CAT receipt by RID"""
    receipt = await cat_chain.get_receipt(rid)
    if receipt:
        return receipt.to_dict()
    raise HTTPException(status_code=404, detail="Receipt not found")

@app.get("/cat/chain/{rid}")
async def get_cat_chain(rid: str):
    """Get complete CAT receipt chain"""
    chain = await cat_chain.get_chain(rid)
    return {"chain": [r.to_dict() for r in chain]}

@app.get("/cat/provenance/{rid}")
async def get_provenance(rid: str):
    """Get complete provenance report"""
    report = await cat_chain.get_provenance_report(rid)
    return report

@app.get("/kg/statistics")
async def get_kg_statistics():
    """Get knowledge graph statistics"""
    stats = kg_integrator.get_statistics()
    return stats

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "version": "3.0.0",
        "semantic_extraction": ENABLE_LLM_EXTRACTION,
        "kg_store": kg_integrator.store_type,
        "llm_model": llm_extractor.model
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8004)