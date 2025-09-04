#!/usr/bin/env python3
"""
Test script for the problematic Token_Fee_Split document
"""

import asyncio
import json
import time
import concurrent.futures
from pathlib import Path
from full_dataset_pipeline import FullDatasetKOIProcessor
import ollama
import logging

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def with_timeout(timeout_seconds):
    """Robust timeout decorator"""
    def decorator(func):
        def wrapper(*args, **kwargs):
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(func, *args, **kwargs)
                try:
                    result = future.result(timeout=timeout_seconds)
                    return result
                except concurrent.futures.TimeoutError:
                    print(f"  ⏰ Timeout after {timeout_seconds}s")
                    raise TimeoutError(f"Operation timed out after {timeout_seconds} seconds")
        return wrapper
    return decorator

def test_direct_mistral_call(content: str):
    """Test direct Mistral API call with the problematic content"""
    print(f"\n🔧 Testing direct Mistral call...")
    print(f"Content length: {len(content)} characters")
    
    client = ollama.Client()
    
    # Create the exact prompt that was causing issues
    content_preview = content[:800] if len(content) > 800 else content
    
    prompt = f"""Extract key entities from this notion document.

Document: Token_Fee_Split_1_0_abfd0e51.md
Content: {content_preview}

Return JSON array with important entities:
[{{"type": "Agent", "name": "Person/Organization"}}, {{"type": "SemanticAsset", "name": "Document/Concept"}}]

Types: Agent, SemanticAsset, EcologicalAsset, GovernanceAct, Question, Claim, Evidence

JSON:"""

    print(f"Prompt length: {len(prompt)} characters")
    
    @with_timeout(30)  # Short timeout for testing
    def make_mistral_call():
        return client.generate(
            model="mistral:7b",
            prompt=prompt,
            format="json",
            options={
                "temperature": 0.1,
                "num_predict": min(1200, max(400, len(content) // 10)),
                "top_k": 20,
                "top_p": 0.8
            },
            stream=False
        )
    
    try:
        start_time = time.time()
        response = make_mistral_call()
        elapsed = time.time() - start_time
        print(f"✅ Mistral call succeeded in {elapsed:.1f}s")
        print(f"Response length: {len(response['response'])} characters")
        print(f"Response: {response['response'][:200]}...")
        return response['response']
    except TimeoutError:
        print(f"❌ Mistral call timed out after 30s")
        return None
    except Exception as e:
        print(f"❌ Mistral call failed: {e}")
        return None

def test_different_prompts(content: str):
    """Test different prompt styles to see which one causes issues"""
    client = ollama.Client()
    
    prompts = [
        # Simple prompt
        "Extract entities from this text in JSON format: " + content[:500],
        
        # Minimal prompt
        "What entities are in this text? Return JSON: " + content[:300],
        
        # Complex prompt (like original)
        f"""Extract key entities from this notion document.

Document: Token_Fee_Split_1_0_abfd0e51.md
Content: {content[:800]}

Return JSON array with important entities:
[{{"type": "Agent", "name": "Person/Organization"}}, {{"type": "SemanticAsset", "name": "Document/Concept"}}]

Types: Agent, SemanticAsset, EcologicalAsset, GovernanceAct, Question, Claim, Evidence

JSON:"""
    ]
    
    for i, prompt in enumerate(prompts, 1):
        print(f"\n🔧 Testing prompt {i} (length: {len(prompt)} chars)...")
        
        @with_timeout(15)
        def make_call():
            return client.generate(
                model="mistral:7b",
                prompt=prompt,
                format="json",
                options={"temperature": 0.1, "num_predict": 400},
                stream=False
            )
        
        try:
            start = time.time()
            response = make_call()
            elapsed = time.time() - start
            print(f"✅ Prompt {i} succeeded in {elapsed:.1f}s")
        except TimeoutError:
            print(f"❌ Prompt {i} timed out")
        except Exception as e:
            print(f"❌ Prompt {i} failed: {e}")

async def test_processor_method(file_path: Path):
    """Test the actual processor method that was hanging"""
    print(f"\n🔧 Testing full processor method...")
    
    processor = FullDatasetKOIProcessor()
    
    try:
        start_time = time.time()
        result = await processor.process_document(file_path)
        elapsed = time.time() - start_time
        
        if result:
            print(f"✅ Processor succeeded in {elapsed:.1f}s")
            print(f"Entities found: {len(result.get('entities', []))}")
            print(f"First entity: {result['entities'][0] if result['entities'] else 'None'}")
        else:
            print(f"❌ Processor returned None after {elapsed:.1f}s")
        
        return result
    except Exception as e:
        print(f"❌ Processor failed: {e}")
        return None

def main():
    print("🌿 KOI Problematic Document Test")
    print("=" * 50)
    
    # Load the problematic document
    doc_path = Path("/Users/darrenzal/projects/RegenAI/GAIA/data/notion/storage/pages/Token_Fee_Split_1_0_abfd0e51.md")
    
    if not doc_path.exists():
        print(f"❌ Document not found: {doc_path}")
        return
    
    content = doc_path.read_text(encoding='utf-8', errors='ignore')
    print(f"📄 Loaded document: {len(content)} characters, {len(content.split())} words")
    
    # Test 1: Direct Mistral call
    test_direct_mistral_call(content)
    
    # Test 2: Different prompt styles  
    test_different_prompts(content)
    
    # Test 3: Full processor method
    print(f"\n🔧 Testing processor method...")
    result = asyncio.run(test_processor_method(doc_path))
    
    print(f"\n✅ All tests completed!")

if __name__ == "__main__":
    main()