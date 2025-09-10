#!/usr/bin/env python3
"""
KOI Pipeline Integration Test
Tests the complete flow from sensor event to searchable embeddings
"""

import asyncio
import json
import sys
import time
from datetime import datetime
from typing import Dict, Any

import httpx
import asyncpg
from colorlog import ColoredFormatter
import logging

# Setup colored logging
handler = logging.StreamHandler()
handler.setFormatter(ColoredFormatter(
    "%(log_color)s%(levelname)-8s%(reset)s %(blue)s%(message)s",
    log_colors={
        'DEBUG': 'cyan',
        'INFO': 'green',
        'WARNING': 'yellow',
        'ERROR': 'red',
        'CRITICAL': 'red,bg_white',
    }
))
logger = logging.getLogger(__name__)
logger.addHandler(handler)
logger.setLevel(logging.INFO)

# Configuration
EVENT_BRIDGE_URL = "http://localhost:8100"
BGE_SERVER_URL = "http://localhost:8090"
POSTGRES_URL = "postgresql://postgres:postgres@localhost:5433/eliza"

class PipelineTest:
    """Test harness for KOI pipeline"""
    
    def __init__(self):
        self.test_results = []
        self.client = httpx.AsyncClient(timeout=30.0)
        
    async def check_service(self, name: str, url: str, endpoint: str = "/") -> bool:
        """Check if a service is running"""
        try:
            response = await self.client.get(f"{url}{endpoint}")
            if response.status_code == 200:
                logger.info(f"{name} is running at {url}")
                return True
            else:
                logger.error(f"{name} returned status {response.status_code}")
                return False
        except Exception as e:
            logger.error(f"{name} is not accessible: {e}")
            return False
    
    async def test_bge_server(self) -> bool:
        """Test BGE embedding generation"""
        try:
            response = await self.client.post(
                f"{BGE_SERVER_URL}/encode",
                json={"text": "test embedding generation"}
            )
            if response.status_code == 200:
                data = response.json()
                embedding = data.get("embedding", [])
                if len(embedding) == 1024:
                    logger.info(f"BGE server generated {len(embedding)}-dim embedding")
                    return True
                else:
                    logger.error(f"Unexpected embedding size: {len(embedding)}")
                    return False
            else:
                # Try with "input" field
                response = await self.client.post(
                    f"{BGE_SERVER_URL}/encode",
                    json={"input": "test embedding generation"}
                )
                if response.status_code == 200:
                    data = response.json()
                    embedding = data.get("embedding", [])
                    logger.info(f"BGE server (input field) generated {len(embedding)}-dim embedding")
                    return len(embedding) == 1024
                else:
                    logger.error(f"BGE server error: {response.status_code}")
                    return False
        except Exception as e:
            logger.error(f"BGE server test failed: {e}")
            return False
    
    async def send_test_event(self, event_type: str = "NEW") -> Dict[str, Any]:
        """Send a test event to the pipeline"""
        test_rid = f"test.pipeline.{datetime.now().strftime('%Y%m%d%H%M%S')}"
        test_event = {
            "event_type": event_type,
            "source_sensor": "test_sensor",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "bundle": {
                "rid": test_rid,
                "cid": f"bafytest{datetime.now().strftime('%Y%m%d%H%M%S')}",
                "content": {
                    "text": f"This is a {event_type} test event for the KOI pipeline. "
                            f"It should be processed, chunked, embedded with BGE, "
                            f"and stored in PostgreSQL for semantic search. "
                            f"Timestamp: {datetime.now().isoformat()}"
                },
                "metadata": {
                    "title": f"Pipeline Test - {event_type}",
                    "test_id": datetime.now().strftime('%Y%m%d%H%M%S')
                },
                "manifest": {
                    "version": "1.0.0",
                    "encoding": "utf-8"
                }
            }
        }
        
        try:
            response = await self.client.post(
                f"{EVENT_BRIDGE_URL}/process-koi-event",
                json=test_event
            )
            if response.status_code == 200:
                result = response.json()
                logger.info(f"Event processed: {result}")
                return {"success": True, "rid": test_rid, "result": result}
            else:
                logger.error(f"Event processing failed: {response.status_code}")
                return {"success": False, "rid": test_rid, "error": response.text}
        except Exception as e:
            logger.error(f"Failed to send event: {e}")
            return {"success": False, "rid": test_rid, "error": str(e)}
    
    async def verify_in_database(self, rid: str) -> bool:
        """Verify that content was stored in database"""
        try:
            conn = await asyncpg.connect(POSTGRES_URL)
            
            # Check isolated tables first
            result = await conn.fetchrow("""
                SELECT COUNT(*) as count
                FROM koi_memories
                WHERE rid = $1 OR rid LIKE $1 || '#chunk%'
            """, rid)
            
            if result and result['count'] > 0:
                logger.info(f"Found {result['count']} memories in isolated tables for RID: {rid}")
                
                # Check for embeddings
                embedding_result = await conn.fetchrow("""
                    SELECT COUNT(*) as count
                    FROM koi_memories km
                    JOIN koi_embeddings ke ON km.id = ke.memory_id
                    WHERE (km.rid = $1 OR km.rid LIKE $1 || '#chunk%')
                    AND ke.dim_1024 IS NOT NULL
                """, rid)
                
                if embedding_result and embedding_result['count'] > 0:
                    logger.info(f"Found {embedding_result['count']} BGE embeddings for RID: {rid}")
                    await conn.close()
                    return True
                else:
                    logger.warning(f"No BGE embeddings found for RID: {rid}")
            else:
                # Check legacy tables
                result = await conn.fetchrow("""
                    SELECT COUNT(*) as count
                    FROM memories m
                    JOIN embeddings e ON m.id = e.memory_id
                    WHERE m.content->>'rid' = $1
                    AND e.dim_1024 IS NOT NULL
                """, rid)
                
                if result and result['count'] > 0:
                    logger.info(f"Found {result['count']} memories in legacy tables for RID: {rid}")
                    await conn.close()
                    return True
                else:
                    logger.warning(f"No memories found for RID: {rid}")
            
            await conn.close()
            return False
            
        except Exception as e:
            logger.error(f"Database verification failed: {e}")
            return False
    
    async def test_deduplication(self) -> bool:
        """Test that duplicate events are handled correctly"""
        # Send first event
        result1 = await self.send_test_event("NEW")
        if not result1["success"]:
            logger.error("First event failed")
            return False
        
        # Send duplicate
        test_rid = result1["rid"]
        duplicate_event = {
            "event_type": "NEW",
            "source_sensor": "test_sensor",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "bundle": {
                "rid": test_rid,  # Same RID
                "cid": f"bafytest{datetime.now().strftime('%Y%m%d%H%M%S')}",
                "content": {
                    "text": "Duplicate content"
                },
                "metadata": {"title": "Duplicate"},
                "manifest": {"version": "1.0.0"}
            }
        }
        
        response = await self.client.post(
            f"{EVENT_BRIDGE_URL}/process-koi-event",
            json=duplicate_event
        )
        
        if response.status_code == 200:
            result = response.json()
            if result.get("chunks_created", 1) == 0:
                logger.info("Deduplication working: duplicate was rejected")
                return True
            else:
                logger.error("Deduplication failed: duplicate was processed")
                return False
        else:
            logger.error(f"Duplicate test failed: {response.status_code}")
            return False
    
    async def run_all_tests(self):
        """Run complete pipeline test suite"""
        logger.info("=" * 60)
        logger.info("KOI Pipeline Integration Test")
        logger.info("=" * 60)
        
        # Test 1: Check services
        logger.info("\n[Test 1] Checking services...")
        event_bridge_ok = await self.check_service("Event Bridge", EVENT_BRIDGE_URL)
        bge_server_ok = await self.check_service("BGE Server", BGE_SERVER_URL, "/encode")
        
        if not event_bridge_ok:
            logger.error("Event Bridge is required. Please start: python koi_event_bridge_v2.py")
            return False
        
        # Test 2: BGE embedding generation
        logger.info("\n[Test 2] Testing BGE embedding generation...")
        if bge_server_ok:
            bge_test = await self.test_bge_server()
            self.test_results.append(("BGE Embedding", bge_test))
        else:
            logger.warning("BGE server not running. Start with: python bge_server.py")
            self.test_results.append(("BGE Embedding", False))
        
        # Test 3: Send NEW event
        logger.info("\n[Test 3] Sending NEW event...")
        new_result = await self.send_test_event("NEW")
        self.test_results.append(("NEW Event", new_result["success"]))
        
        if new_result["success"]:
            # Wait for processing
            logger.info("Waiting for database propagation...")
            await asyncio.sleep(2)
            
            # Test 4: Verify in database
            logger.info("\n[Test 4] Verifying database storage...")
            db_ok = await self.verify_in_database(new_result["rid"])
            self.test_results.append(("Database Storage", db_ok))
            
            # Test 5: Deduplication
            logger.info("\n[Test 5] Testing deduplication...")
            dedup_ok = await self.test_deduplication()
            self.test_results.append(("Deduplication", dedup_ok))
            
            # Test 6: UPDATE event
            logger.info("\n[Test 6] Testing UPDATE event...")
            update_result = await self.send_test_event("UPDATE")
            self.test_results.append(("UPDATE Event", update_result["success"]))
        
        # Print summary
        logger.info("\n" + "=" * 60)
        logger.info("Test Summary")
        logger.info("=" * 60)
        
        passed = 0
        failed = 0
        for test_name, result in self.test_results:
            status = "✓ PASSED" if result else "✗ FAILED"
            color = "\033[92m" if result else "\033[91m"
            logger.info(f"{color}{status}\033[0m - {test_name}")
            if result:
                passed += 1
            else:
                failed += 1
        
        logger.info("-" * 60)
        logger.info(f"Total: {passed} passed, {failed} failed")
        
        if failed == 0:
            logger.info("\n🎉 All tests passed! Pipeline is working correctly.")
            return True
        else:
            logger.error(f"\n⚠️  {failed} test(s) failed. Please check the logs above.")
            return False

async def main():
    """Run the test suite"""
    tester = PipelineTest()
    success = await tester.run_all_tests()
    await tester.client.aclose()
    
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("\nTest interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Test failed with error: {e}")
        sys.exit(1)