#!/usr/bin/env python3
"""
Entity-Chunk Linker via Character Offsets
Maps extracted entities to vector embedding chunks without burning tokens on chunk IDs
"""
from typing import List, Dict, Any, Optional, Tuple
import re

try:
    from rapidfuzz import fuzz
    HAS_RAPIDFUZZ = True
except ImportError:
    HAS_RAPIDFUZZ = False
    # Simple fallback fuzzy matching
    class fuzz:
        @staticmethod
        def partial_ratio(s1, s2):
            # Simple substring matching score
            s1, s2 = s1.lower(), s2.lower()
            if s1 in s2 or s2 in s1:
                return 90
            return 0

        @staticmethod
        def ratio(s1, s2):
            s1, s2 = s1.lower(), s2.lower()
            if s1 == s2:
                return 100
            if s1 in s2 or s2 in s1:
                return 80
            return 0


class EntityChunkLinker:
    """
    Links entities from KG extraction to embedding chunks via character offsets

    Architecture:
    1. Chunks are pre-created for embeddings with (chunk_id, start_char, end_char)
    2. Entities are extracted from CLEAN full text (no chunk IDs)
    3. Post-processing maps entity mentions → character offsets → chunk IDs
    """

    def __init__(self, fuzzy_threshold: int = 85):
        """
        Args:
            fuzzy_threshold: Minimum similarity score (0-100) for fuzzy matching
        """
        self.fuzzy_threshold = fuzzy_threshold

    def create_chunks_with_offsets(
        self,
        full_text: str,
        chunk_size: int = 1000,
        overlap: int = 100,
        doc_id: str = None
    ) -> List[Dict[str, Any]]:
        """
        Split document into chunks with character offset tracking

        Args:
            full_text: Complete document text
            chunk_size: Characters per chunk
            overlap: Overlap between chunks (to prevent splitting entities)
            doc_id: Document identifier

        Returns:
            List of chunk dicts with metadata
        """
        chunks = []
        start = 0
        chunk_idx = 0

        while start < len(full_text):
            end = min(start + chunk_size, len(full_text))

            # Try to break at sentence boundary if not at document end
            if end < len(full_text):
                # Look for sentence endings in last 100 chars
                search_start = max(start, end - 100)
                sentence_endings = [
                    m.end() for m in re.finditer(r'[.!?]\s+', full_text[search_start:end])
                ]
                if sentence_endings:
                    end = search_start + sentence_endings[-1]

            chunk_text = full_text[start:end]

            chunks.append({
                'chunk_id': f"{doc_id}_chunk_{chunk_idx}" if doc_id else f"chunk_{chunk_idx}",
                'doc_id': doc_id,
                'text': chunk_text,
                'start_char': start,
                'end_char': end,
                'size': end - start,
                'chunk_index': chunk_idx
            })

            # Move to next chunk with overlap
            start = end - overlap
            chunk_idx += 1

        return chunks

    def find_entity_mentions(
        self,
        entity_name: str,
        full_text: str,
        use_fuzzy: bool = True
    ) -> List[Dict[str, int]]:
        """
        Find all occurrences of entity in text

        Args:
            entity_name: Entity to find (e.g., "Gregory Landua")
            full_text: Full document text
            use_fuzzy: Use fuzzy matching for variations

        Returns:
            List of {start, end, match_text, score} dicts
        """
        mentions = []

        # Exact matches first (case-insensitive)
        pattern = re.escape(entity_name)
        for match in re.finditer(pattern, full_text, re.IGNORECASE):
            mentions.append({
                'start': match.start(),
                'end': match.end(),
                'match_text': match.group(),
                'score': 100  # Perfect match
            })

        if use_fuzzy and not mentions:
            # Fuzzy matching for paraphrases
            # Split text into candidate phrases (entity might be slightly different)
            words = entity_name.split()
            candidate_length = len(entity_name)

            # Sliding window of similar length
            for i in range(0, len(full_text) - candidate_length + 1):
                candidate = full_text[i:i + candidate_length + 50]  # +50 buffer

                # Fast check: does it contain most of the words?
                if sum(word.lower() in candidate.lower() for word in words) < len(words) * 0.6:
                    continue

                # Detailed fuzzy match
                score = fuzz.partial_ratio(entity_name.lower(), candidate.lower())

                if score >= self.fuzzy_threshold:
                    # Find exact boundaries of matched text
                    # Use token_set_ratio to find best substring
                    best_match = self._find_best_substring(
                        entity_name,
                        candidate,
                        min_score=self.fuzzy_threshold
                    )

                    if best_match:
                        mentions.append({
                            'start': i + best_match['start'],
                            'end': i + best_match['end'],
                            'match_text': best_match['text'],
                            'score': best_match['score']
                        })

        # Deduplicate overlapping mentions (keep highest score)
        mentions = self._deduplicate_mentions(mentions)

        return mentions

    def _find_best_substring(
        self,
        entity: str,
        text: str,
        min_score: int = 85
    ) -> Optional[Dict[str, Any]]:
        """Find best matching substring in text"""
        best = None
        entity_len = len(entity)

        for i in range(len(text) - entity_len + 1):
            for j in range(i + entity_len, min(i + entity_len + 50, len(text) + 1)):
                candidate = text[i:j]
                score = fuzz.ratio(entity.lower(), candidate.lower())

                if score >= min_score and (not best or score > best['score']):
                    best = {
                        'start': i,
                        'end': j,
                        'text': candidate,
                        'score': score
                    }

        return best

    def _deduplicate_mentions(
        self,
        mentions: List[Dict[str, int]]
    ) -> List[Dict[str, int]]:
        """Remove overlapping mentions, keeping highest score"""
        if not mentions:
            return []

        # Sort by start position
        sorted_mentions = sorted(mentions, key=lambda x: (x['start'], -x['score']))

        result = []
        for mention in sorted_mentions:
            # Check if overlaps with any existing mention
            overlaps = False
            for existing in result:
                if (mention['start'] < existing['end'] and
                    mention['end'] > existing['start']):
                    # Overlaps - keep higher score
                    if mention['score'] > existing['score']:
                        result.remove(existing)
                    else:
                        overlaps = True
                        break

            if not overlaps:
                result.append(mention)

        return sorted(result, key=lambda x: x['start'])

    def map_mention_to_chunk(
        self,
        char_offset: int,
        chunks: List[Dict[str, Any]]
    ) -> Optional[str]:
        """
        Find which chunk contains a character offset

        Args:
            char_offset: Character position in full text
            chunks: List of chunk dicts with start_char/end_char

        Returns:
            chunk_id or None
        """
        for chunk in chunks:
            if chunk['start_char'] <= char_offset < chunk['end_char']:
                return chunk['chunk_id']

        return None

    def link_entities_to_chunks(
        self,
        entities: List[Dict[str, Any]],
        full_text: str,
        chunks: List[Dict[str, Any]],
        use_fuzzy: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Main linking function: map entities to chunks via character offsets

        Args:
            entities: List of extracted entities from LLM
            full_text: Complete document text
            chunks: Pre-created chunks with offsets
            use_fuzzy: Enable fuzzy matching

        Returns:
            Enhanced entities with chunk_ids and offsets
        """
        enhanced_entities = []

        for entity in entities:
            entity_name = entity.get('name', '')
            if not entity_name:
                continue

            # Find all mentions of this entity in text
            mentions = self.find_entity_mentions(
                entity_name,
                full_text,
                use_fuzzy=use_fuzzy
            )

            # Map each mention to chunk
            chunk_ids = []
            offsets = []

            for mention in mentions:
                chunk_id = self.map_mention_to_chunk(mention['start'], chunks)

                if chunk_id and chunk_id not in chunk_ids:
                    chunk_ids.append(chunk_id)

                offsets.append({
                    'start': mention['start'],
                    'end': mention['end'],
                    'chunk_id': chunk_id,
                    'match_text': mention['match_text'],
                    'score': mention['score']
                })

            # Add linking metadata to entity
            enhanced_entity = entity.copy()
            enhanced_entity['chunk_ids'] = chunk_ids
            enhanced_entity['offsets'] = offsets
            enhanced_entity['mention_count'] = len(mentions)

            enhanced_entities.append(enhanced_entity)

        return enhanced_entities


def example_usage():
    """Demonstrate the offset-mapping workflow"""

    # Example full document (podcast transcript)
    full_text = """
    Gregory Landua is a co-founder of Regen Network. He discusses the concept of
    regenerative finance and how it aligns with ecological health. Gregory believes
    that ReFi can transform how we think about capital allocation.

    As he mentioned earlier in the conversation, the Apollo Project serves as
    inspiration for what's possible when we align resources with ambitious goals.
    The regenerative finance movement aims to do something similar for ecological
    restoration.
    """

    linker = EntityChunkLinker(fuzzy_threshold=85)

    # Step 1: Create chunks with offsets (for embeddings)
    print("=" * 70)
    print("STEP 1: Create Chunks with Character Offsets")
    print("=" * 70)

    chunks = linker.create_chunks_with_offsets(
        full_text,
        chunk_size=150,
        overlap=20,
        doc_id="podcast_ep_23"
    )

    for chunk in chunks:
        print(f"\n{chunk['chunk_id']}:")
        print(f"  Chars: {chunk['start_char']}-{chunk['end_char']}")
        print(f"  Text: {chunk['text'][:60]}...")

    # Step 2: Extract entities from CLEAN full text (no chunk IDs)
    print("\n" + "=" * 70)
    print("STEP 2: Extract Entities (simulated LLM output)")
    print("=" * 70)

    # Simulated LLM extraction (in reality, this comes from GPT-4)
    entities = [
        {
            "name": "Gregory Landua",
            "type": "PERSON",
            "description": "Co-founder of Regen Network"
        },
        {
            "name": "regenerative finance",
            "type": "CONCEPT",
            "description": "Financial system aligned with ecological health"
        },
        {
            "name": "Apollo Project",
            "type": "PROJECT",
            "description": "Historical reference for ambitious goals"
        }
    ]

    for entity in entities:
        print(f"\n{entity['name']} ({entity['type']})")
        print(f"  {entity['description']}")

    # Step 3: Link entities to chunks via character offsets
    print("\n" + "=" * 70)
    print("STEP 3: Link Entities to Chunks (Post-Processing)")
    print("=" * 70)

    enhanced_entities = linker.link_entities_to_chunks(
        entities,
        full_text,
        chunks,
        use_fuzzy=True
    )

    for entity in enhanced_entities:
        print(f"\n{entity['name']}:")
        print(f"  Type: {entity['type']}")
        print(f"  Mentions: {entity['mention_count']}")
        print(f"  Chunks: {entity['chunk_ids']}")
        print(f"  Offsets:")
        for offset in entity['offsets']:
            print(f"    - Chars {offset['start']}-{offset['end']}: \"{offset['match_text']}\"")
            print(f"      Chunk: {offset['chunk_id']} (score: {offset['score']})")

    # Step 4: Demonstrate retrieval
    print("\n" + "=" * 70)
    print("STEP 4: Retrieval Example")
    print("=" * 70)

    query = "What does Gregory Landua say about ReFi?"
    print(f"\nUser Query: {query}")
    print("\n[1] Query Knowledge Graph:")
    print("    → Entity: 'Gregory Landua' (PERSON)")
    print("    → Relationship: DISCUSSES → 'regenerative finance'")
    print("    → Chunks: ['podcast_ep_23_chunk_0', 'podcast_ep_23_chunk_1']")

    print("\n[2] Retrieve Chunk Texts:")
    target_chunks = [c for c in chunks if c['chunk_id'] in ['podcast_ep_23_chunk_0', 'podcast_ep_23_chunk_1']]
    for chunk in target_chunks:
        print(f"\n  {chunk['chunk_id']}:")
        print(f"    {chunk['text'].strip()}")

    print("\n[3] Combined Result:")
    print("    'Gregory Landua discusses regenerative finance in Episode 23.'")
    print("    Evidence: [Chunk 0, chars 5-20] 'Gregory Landua is a co-founder...'")


if __name__ == '__main__':
    example_usage()
