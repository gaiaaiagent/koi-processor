#!/usr/bin/env python3
"""
Debug Mistral JSON Response
See what Mistral is actually returning
"""

import ollama
import json
from pathlib import Path

# Read a sample document
sample_file = Path("/Users/darrenzal/projects/RegenAI/GAIA/data/notion/storage/pages").glob("*.md").__next__()
content = sample_file.read_text(encoding='utf-8', errors='ignore')[:800]

print(f"🔍 Debugging Mistral with: {sample_file.name}")
print(f"📄 Content length: {len(content)} chars")
print("-" * 50)

# Simplified prompt
prompt = f"""Extract entities from this text and return ONLY valid JSON array:

Text: {content}

Return format:
[
  {{
    "type": "Agent",
    "name": "Entity Name"
  }}
]

JSON:"""

client = ollama.Client()

print("🤖 Calling Mistral...")
response = client.generate(
    model="mistral:7b",
    prompt=prompt,
    format="json",
    options={
        "temperature": 0.1,
        "num_predict": 500
    },
    stream=False
)

result = response['response']
print("📥 Raw Mistral Response:")
print(repr(result))
print()
print("📄 Formatted Response:")
print(result)
print()

# Try to parse
print("🔧 Attempting to parse...")
try:
    parsed = json.loads(result)
    print("✅ Successfully parsed JSON!")
    print(f"📊 Type: {type(parsed)}")
    print(f"📦 Content: {parsed}")
except json.JSONDecodeError as e:
    print(f"❌ JSON parsing failed: {e}")
    print(f"🔍 Error at position {e.pos}: '{result[max(0, e.pos-10):e.pos+10]}'")
    
    # Try to fix common issues
    fixed = result.replace("'", '"')  # Replace single quotes
    print(f"🔧 Trying with fixed quotes...")
    try:
        parsed = json.loads(fixed)
        print("✅ Fixed version parsed!")
        print(f"📦 Content: {parsed}")
    except:
        print("❌ Still couldn't parse")