"""
A/B Test: Validate prevention improvements (filters + prompts).

Compares extraction quality using improved prompts/filters on a sampled set of documents.
Metrics:
- Generic PERSON rate (target: <1%)
- CONCEPT coverage (target: >2%)
"""

import asyncio
import psycopg2
from typing import List, Dict

from src.extraction.openai_extractor import OpenAIExtractor
from src.knowledge_graph.improvements import EntityQualityFilter


SAMPLE_SOURCES = {
    "any": 100,  # sample generic content since source metadata is missing
}


def sample_documents(source_tag: str, count: int) -> List[Dict]:
    """Sample random documents from koi_content (source metadata may be missing)."""

    conn = psycopg2.connect(
        host="localhost",
        port=5433,
        database="eliza",
        user="postgres",
        password="postgres",
    )
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT rid, COALESCE(raw_content, ''), COALESCE(source_rid, '')
        FROM koi_content
        WHERE raw_content IS NOT NULL
          AND length(raw_content) > 0
        ORDER BY RANDOM()
        LIMIT %s
        """,
        (count,),
    )
    docs = [{"id": row[0], "content": row[1], "source": row[2]} for row in cursor.fetchall()]
    conn.close()
    return docs


async def extract_with_improvements(documents: List[Dict]) -> Dict:
    """Extract using improved prompts/filters."""
    extractor = OpenAIExtractor(model="gpt-4o-mini")
    quality_filter = EntityQualityFilter()

    all_entities: List[Dict] = []
    for doc in documents:
        entities = await extractor.extract_entities(doc["content"])
        # Normalize interface: expect list of dicts with name/type/confidence
        filtered, _ = quality_filter.get_filtered_with_reasons(entities)
        all_entities.extend(filtered)

    return analyze_entities(all_entities)


def analyze_entities(entities: List[Dict]) -> Dict:
    """Compute quality metrics."""
    total = len(entities)
    type_counts = {}
    for e in entities:
        etype = e.get("type") or e.get("entity_type") or "UNKNOWN"
        type_counts[etype] = type_counts.get(etype, 0) + 1

    generic_terms = {
        "buyers",
        "sellers",
        "partners",
        "users",
        "members",
        "contributors",
        "stakeholders",
        "participants",
        "investors",
        "validators",
        "administrators",
        "developers",
        "moderators",
        "water utilities",
        "providers",
        "suppliers",
    }
    generic_person_count = sum(
        1
        for e in entities
        if (e.get("type") or e.get("entity_type")) == "PERSON"
        and (e.get("name") or e.get("entity_text") or "").lower() in generic_terms
    )

    concept_count = type_counts.get("CONCEPT", 0)
    concept_rate = (concept_count / total * 100) if total else 0
    person_count = type_counts.get("PERSON", 0)
    generic_rate = (generic_person_count / person_count * 100) if person_count else 0

    return {
        "total_entities": total,
        "type_distribution": type_counts,
        "generic_person_count": generic_person_count,
        "generic_person_rate": generic_rate,
        "concept_count": concept_count,
        "concept_rate": concept_rate,
    }


async def main():
    print("A/B Test: Prevention Validation")
    print("=" * 60)

    all_docs: List[Dict] = []
    for source, count in SAMPLE_SOURCES.items():
        docs = sample_documents(source, count)
        all_docs.extend(docs)
        print(f"✓ Sampled {len(docs)} from {source}")

    print(f"\nTotal documents: {len(all_docs)}")

    print("\nExtracting with improved pipeline...")
    results = await extract_with_improvements(all_docs)

    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60)
    print(f"Total entities: {results['total_entities']}")
    print("\nType distribution:")
    for etype, count in sorted(results["type_distribution"].items(), key=lambda x: -x[1]):
        pct = count / results["total_entities"] * 100 if results["total_entities"] else 0
        print(f"  {etype:<15} {count:>5} ({pct:>5.1f}%)")

    print("\nQuality Metrics:")
    print(f"  Generic PERSON rate: {results['generic_person_rate']:.2f}% (target: <1%)")
    print(f"  CONCEPT coverage: {results['concept_rate']:.2f}% (target: >2%)")

    passed = results["generic_person_rate"] < 1.0 and results["concept_rate"] > 2.0
    print(f"\nStatus: {'✅ PASS' if passed else '❌ FAIL'}")
    if passed:
        print("\n→ Improvements are effective. Proceed to targeted re-extraction.")
    else:
        print("\n→ Improvements need tuning. Adjust filter/prompt and retest.")


if __name__ == "__main__":
    asyncio.run(main())
