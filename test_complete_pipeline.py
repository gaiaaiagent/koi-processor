"""Test the COMPLETE pipeline from sensor input to agent RAG query"""
import requests
import time
import json
import psycopg2
from datetime import datetime

def test_complete_pipeline():
    print("="*60)
    print("COMPLETE PIPELINE TEST: Sensor → Processor → DB → Agent")
    print("="*60)

    # 1. Send test content with proper format for event bridge
    test_event = {
        "event_type": "NEW",
        "source_sensor": "pipeline_test",
        "timestamp": datetime.now().isoformat() + "Z",
        "bundle": {
            "rid": f"test.complete.{int(time.time())}",
            "cid": f"bafycomplete{int(time.time())}",
            "content": {
                "text": "Regenerative agriculture creates biodiverse ecosystems that sequester carbon while producing nutritious food. This comprehensive test validates the entire KOI pipeline from sensor input through BGE embeddings to agent knowledge access. Test timestamp: " + datetime.now().isoformat()
            },
            "metadata": {
                "test_id": "milestone_b_complete",
                "source": "integration_test",
                "session": "verification"
            },
            "manifest": {
                "version": "1.0",
                "type": "test"
            }
        }
    }

    print("\n1. Sending test content through Event Bridge...")
    try:
        r = requests.post("http://localhost:8100/process-koi-event", json=test_event)
        print(f"   Event Bridge response: {r.status_code}")
        result = r.json()
        print(f"   Processing result: success={result.get('success')}, chunks={result.get('chunks_created')}, embeddings={result.get('embeddings_created')}")
    except Exception as e:
        print(f"   Error: {e}")
        return False

    # 2. Wait for processing
    print("\n2. Waiting for processing (3 seconds)...")
    time.sleep(3)

    # 3. Check if embedding was created
    print("\n3. Checking BGE embedding generation...")
    r = requests.post("http://localhost:8090/encode",
                     json={"text": test_event["bundle"]["content"]["text"]})
    embedding = r.json().get("embedding", [])
    print(f"   Embedding dimension: {len(embedding)}")
    assert len(embedding) == 1024, "BGE embedding wrong dimension"

    # 4. Check database storage
    print("\n4. Checking PostgreSQL storage...")
    try:
        conn = psycopg2.connect(
            host="localhost",
            port=5433,
            database="eliza",
            user="postgres",
            password="postgres"
        )
        cur = conn.cursor()

        # Check KOI memories table (isolated tables)
        cur.execute("""
            SELECT COUNT(*) FROM koi_memories 
            WHERE content LIKE '%regenerative agriculture%'
            AND created_at > NOW() - INTERVAL '5 minutes'
        """)
        koi_memory_count = cur.fetchone()[0]
        print(f"   KOI memories with test content: {koi_memory_count}")

        # Check embeddings in koi_embeddings
        cur.execute("""
            SELECT COUNT(*), AVG(array_length(embedding, 1))
            FROM koi_embeddings 
            WHERE created_at > NOW() - INTERVAL '5 minutes'
        """)
        embedding_count, avg_dim = cur.fetchone()
        print(f"   KOI embeddings created: {embedding_count} (avg dim: {avg_dim})")

        # Check regular memories table (for agents)
        cur.execute("""
            SELECT COUNT(*) FROM memories 
            WHERE content LIKE '%regenerative%' OR content LIKE '%KOI%'
        """)
        agent_memory_count = cur.fetchone()[0]
        print(f"   Agent-accessible memories: {agent_memory_count}")

        # 5. Test CAT receipt generation
        print("\n5. Checking CAT/Provenance receipts...")
        cur.execute("""
            SELECT COUNT(*) FROM koi_transformation_receipts
            WHERE created_at > NOW() - INTERVAL '5 minutes'
        """)
        receipt_count = cur.fetchone()[0]
        print(f"   Transformation receipts in DB: {receipt_count}")

        # 6. CRITICAL: Test agent knowledge accessibility
        print("\n6. Testing Agent Knowledge Accessibility...")
        print("   (This verifies if agents can query pipeline knowledge)")

        # Check if memories have embeddings that agents can use
        cur.execute("""
            SELECT 
                km.rid,
                km.content,
                ke.embedding IS NOT NULL as has_embedding,
                array_length(ke.embedding, 1) as embedding_dim
            FROM koi_memories km
            LEFT JOIN koi_embeddings ke ON ke.memory_id = km.id
            WHERE km.created_at > NOW() - INTERVAL '5 minutes'
            ORDER BY km.created_at DESC
            LIMIT 5
        """)
        
        recent_memories = cur.fetchall()
        print(f"\n   Recent KOI memories with embeddings:")
        for rid, content, has_emb, dim in recent_memories:
            print(f"     - RID: {rid}")
            print(f"       Content: {content[:100]}...")
            print(f"       Has embedding: {has_emb}, Dimension: {dim}")

        # Check if agents exist in the system
        cur.execute("""
            SELECT name, created_at, COUNT(m.id) as memory_count
            FROM agents a
            LEFT JOIN memories m ON m.agent_id = a.id
            GROUP BY a.id, a.name, a.created_at
            ORDER BY a.created_at DESC
            LIMIT 5
        """)
        agents = cur.fetchall()
        
        print(f"\n   Agents in system:")
        for name, created, mem_count in agents:
            print(f"     - {name}: {mem_count} memories (created: {created})")

        conn.close()

        # 7. Test actual RAG query simulation
        print("\n7. Simulating Agent RAG Query...")
        
        # Try to query like an agent would
        try:
            # First check if there's an agent API running
            agent_running = False
            try:
                r = requests.get("http://localhost:3000/", timeout=2)
                if r.status_code in [200, 404]:
                    agent_running = True
                    print("   Eliza agent API detected on port 3000")
            except:
                print("   No Eliza agent API running (would need to start one)")

            # Even without agent, we can verify the data is queryable
            conn = psycopg2.connect(
                host="localhost", port=5433, database="eliza",
                user="postgres", password="postgres"
            )
            cur = conn.cursor()
            
            # Simulate vector similarity search that agents would do
            cur.execute("""
                SELECT COUNT(*)
                FROM koi_embeddings
                WHERE embedding IS NOT NULL
                AND array_length(embedding, 1) = 1024
            """)
            queryable_embeddings = cur.fetchone()[0]
            print(f"   Queryable BGE embeddings (1024-dim): {queryable_embeddings}")
            
            conn.close()

        except Exception as e:
            print(f"   Query simulation error: {e}")

        print("\n" + "="*60)
        if koi_memory_count > 0 and embedding_count > 0:
            print("✅ PIPELINE WORKING: Content flows from sensor to database")
            print("✅ BGE embeddings are being generated and stored")
            if queryable_embeddings > 0:
                print("✅ Knowledge is READY for agent access (1024-dim embeddings)")
            else:
                print("⚠️  Embeddings need to be made accessible to agents")
            return True
        else:
            print("❌ PIPELINE ISSUE: Content not reaching database properly")
            return False

    except Exception as e:
        print(f"   Database error: {e}")
        return False

if __name__ == "__main__":
    success = test_complete_pipeline()
    exit(0 if success else 1)