#!/usr/bin/env python3
"""
Extract concept definitions from Regen Network spec files.

Parses markdown files to extract concept names, descriptions, and keywords.
"""

import re
import hashlib
from pathlib import Path
from typing import List, Dict, Set
from loguru import logger

# Common stopwords to filter out from keywords
STOPWORDS = {
    'the', 'a', 'an', 'is', 'are', 'was', 'were', 'of', 'to', 'for', 'and', 
    'or', 'in', 'on', 'at', 'by', 'with', 'from', 'this', 'that', 'these', 
    'those', 'be', 'been', 'being', 'have', 'has', 'had', 'can', 'will',
    'would', 'could', 'should', 'may', 'might', 'must', 'shall', 'it', 'its',
    'into', 'through', 'during', 'before', 'after', 'above', 'below', 'as'
}

def extract_keywords(name: str, description: str, max_keywords: int = 15) -> List[str]:
    """
    Extract keywords from concept name and description.
    
    Args:
        name: Concept name
        description: Concept description
        max_keywords: Maximum number of keywords to return
        
    Returns:
        List of keywords (lowercased)
    """
    # Combine name and description
    text = f"{name} {description}"
    
    # Remove special characters and split into words
    words = re.findall(r'\b[a-zA-Z]{2,}\b', text.lower())
    
    # Count word frequencies
    word_counts = {}
    for word in words:
        if word not in STOPWORDS and len(word) > 2:
            word_counts[word] = word_counts.get(word, 0) + 1
    
    # Sort by frequency and take top keywords
    sorted_words = sorted(word_counts.items(), key=lambda x: (-x[1], x[0]))
    keywords = [word for word, count in sorted_words[:max_keywords]]
    
    # Always include words from the concept name itself
    name_words = [w.lower() for w in re.findall(r'\b[a-zA-Z]{2,}\b', name)]
    for word in name_words:
        if word not in keywords and word not in STOPWORDS:
            keywords.insert(0, word)
    
    return keywords[:max_keywords]


def extract_concepts_from_spec(spec_path: Path, module_name: str) -> List[Dict]:
    """
    Extract concept definitions from a spec file.
    
    Looks for ### headings followed by descriptions.
    
    Args:
        spec_path: Path to spec markdown file
        module_name: Name of the module (e.g., 'ecocredit', 'data')
        
    Returns:
        List of concept dictionaries
    """
    logger.info(f"Parsing {spec_path}...")
    
    content = spec_path.read_text()
    concepts = []
    
    # Pattern: ### Concept Name followed by description
    # Match until next ### or ## or end of string
    pattern = r'###\s+([A-Z][^\n]+)\n\n(.+?)(?=\n###|\n##|\Z)'
    matches = re.findall(pattern, content, re.DOTALL)
    
    for name, description in matches:
        name = name.strip()
        # Clean up description - take first 500 chars
        description = ' '.join(description.strip().split())[:500]
        
        # Skip if description is too short (likely not a real concept)
        if len(description) < 50:
            continue
        
        # Generate unique concept ID
        concept_id = f"concept_{module_name}_{name.lower().replace(' ', '_')}"
        concept_id = re.sub(r'[^a-z0-9_]', '', concept_id)
        
        # Extract keywords
        keywords = extract_keywords(name, description)
        
        concept = {
            'id': concept_id,
            'name': name,
            'description': description,
            'keywords': keywords,
            'source': str(spec_path),
            'module': module_name,
            'domain': 'regen-network'
        }
        
        concepts.append(concept)
        logger.debug(f"  Extracted: {name} ({len(keywords)} keywords)")
    
    logger.info(f"  Found {len(concepts)} concepts in {spec_path.name}")
    return concepts


def extract_all_concepts(repo_path: Path) -> List[Dict]:
    """
    Extract all concepts from spec files in the repository.
    
    Args:
        repo_path: Path to regen-ledger repository
        
    Returns:
        List of all extracted concepts
    """
    logger.info("Extracting concepts from spec files...")
    
    all_concepts = []
    
    # Find all spec files in x/*/spec/
    x_dir = repo_path / 'x'
    if not x_dir.exists():
        logger.error(f"Directory not found: {x_dir}")
        return []
    
    # Find all *_concepts.md files
    spec_files = list(x_dir.glob('*/spec/*_concepts.md'))
    spec_files += list(x_dir.glob('*/spec/01_concepts.md'))
    
    # Also check nested submodules
    spec_files += list(x_dir.glob('*/*/spec/*_concepts.md'))
    spec_files += list(x_dir.glob('*/*/spec/01_concepts.md'))
    
    spec_files = sorted(set(spec_files))
    
    logger.info(f"Found {len(spec_files)} concept spec files")
    
    for spec_file in spec_files:
        # Extract module name from path
        # e.g., x/ecocredit/spec/01_concepts.md -> ecocredit
        # e.g., x/ecocredit/basket/spec/01_concepts.md -> ecocredit_basket
        parts = spec_file.relative_to(x_dir).parts
        if len(parts) >= 3 and parts[1] == 'spec':
            module_name = parts[0]
        elif len(parts) >= 4 and parts[2] == 'spec':
            module_name = f"{parts[0]}_{parts[1]}"
        else:
            module_name = parts[0]
        
        concepts = extract_concepts_from_spec(spec_file, module_name)
        all_concepts.extend(concepts)
    
    logger.info(f"Extracted {len(all_concepts)} total concepts")
    
    # Print summary
    logger.info("\nConcept summary by module:")
    module_counts = {}
    for concept in all_concepts:
        module = concept['module']
        module_counts[module] = module_counts.get(module, 0) + 1
    
    for module, count in sorted(module_counts.items()):
        logger.info(f"  {module}: {count} concepts")
    
    return all_concepts


if __name__ == '__main__':
    import sys
    import json
    
    if len(sys.argv) < 2:
        print("Usage: python extract_concepts.py <repo_path> [output_json]")
        sys.exit(1)
    
    repo_path = Path(sys.argv[1])
    output_file = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    
    # Extract concepts
    concepts = extract_all_concepts(repo_path)
    
    # Save to JSON if output file specified
    if output_file:
        output_file.write_text(json.dumps(concepts, indent=2))
        logger.info(f"Saved {len(concepts)} concepts to {output_file}")
    else:
        # Print to stdout
        print(json.dumps(concepts, indent=2))
    
    # Print some examples
    logger.info("\nExample concepts:")
    for concept in concepts[:3]:
        logger.info(f"  - {concept['name']}: {concept['description'][:80]}...")
        logger.info(f"    Keywords: {', '.join(concept['keywords'][:5])}")
