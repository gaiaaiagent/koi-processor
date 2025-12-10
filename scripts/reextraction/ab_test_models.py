#!/usr/bin/env python3
"""
A/B Test: GPT-4o-mini vs GPT-4.1-mini for Entity Extraction

Compares extraction quality and cost between models on a sample of documents.
"""

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Any

import psycopg2
from dotenv import load_dotenv

# Add src directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))


class ModelABTest:
    """A/B test comparing two OpenAI models for entity extraction."""

    def __init__(self, db_config: Dict[str, str], api_key: str):
        self.db_config = db_config
        self.api_key = api_key
        self.results = {
            "gpt-4o-mini": {
                "model": "gpt-4o-mini",
                "documents": [],
                "total_entities": 0,
                "passed_entities": 0,
                "blocked_entities": 0,
                "cost": {"input": 0, "output": 0, "total": 0},
                "errors": 0
            },
            "gpt-4.1-mini": {
                "model": "gpt-4.1-mini",
                "documents": [],
                "total_entities": 0,
                "passed_entities": 0,
                "blocked_entities": 0,
                "cost": {"input": 0, "output": 0, "total": 0},
                "errors": 0
            }
        }

    def get_sample_documents(self, sample_size: int = 50) -> List[Dict[str, Any]]:
        """Get a random sample of documents for testing.

        Uses documents that have already been extracted for fair comparison.
        """
        conn = psycopg2.connect(**self.db_config)
        cursor = conn.cursor()

        # Get documents that HAVE been extracted (for fair comparison)
        # Use Discourse documents which have the most extractions
        query = """
        SELECT
            m.rid,
            m.content->>'text' as text,
            m.metadata->>'source' as source,
            char_length(m.content->>'text') as text_length
        FROM koi_memories m
        INNER JOIN koi_kg_extractions e ON m.rid = e.memory_rid
        WHERE metadata->>'source' = 'discourse:forum.regen.network'
        AND char_length(m.content->>'text') > 200
        AND char_length(m.content->>'text') < 8000
        ORDER BY RANDOM()
        LIMIT %s
        """

        cursor.execute(query, (sample_size,))
        docs = []
        for row in cursor.fetchall():
            docs.append({
                "rid": row[0],
                "text": row[1],
                "source": row[2],
                "length": row[3]
            })

        cursor.close()
        conn.close()

        return docs

    async def extract_with_model(
        self,
        model: str,
        documents: List[Dict[str, Any]]
    ) -> None:
        """Extract entities from documents using specified model."""
        # Import here to avoid module path issues
        from extraction.openai_extractor import OpenAIExtractor
        from knowledge_graph.graph_integration import KnowledgeGraphIntegrator

        print(f"\n{'='*60}")
        print(f"Testing Model: {model}")
        print(f"{'='*60}\n")

        extractor = OpenAIExtractor(
            api_key=self.api_key,
            model=model
        )

        # Initialize pipeline
        kg = KnowledgeGraphIntegrator(
            store_type="memory",
            use_pipeline=True
        )

        for i, doc in enumerate(documents, 1):
            print(f"[{i}/{len(documents)}] Extracting from document {doc['rid'][:8]}...")

            try:
                # Extract entities
                result = await extractor.extract_metadata(
                    content=doc["text"],
                    source_type=doc["source"],
                    existing_metadata={"rid": doc["rid"]}
                )

                if "error" in result:
                    print(f"  ❌ Error: {result['error']}")
                    self.results[model]["errors"] += 1
                    continue

                entities = result.get("entities", [])
                input_tokens = result.get("usage", {}).get("prompt_tokens", 0)
                output_tokens = result.get("usage", {}).get("completion_tokens", 0)

                # Estimate tokens if not provided (fallback)
                if input_tokens == 0:
                    input_tokens = len(doc["text"]) // 4  # Rough estimate
                if output_tokens == 0:
                    output_tokens = len(json.dumps(entities)) // 4

                # Calculate cost
                cost = self.calculate_cost(model, input_tokens, output_tokens)
                self.results[model]["cost"]["input"] += cost["input"]
                self.results[model]["cost"]["output"] += cost["output"]
                self.results[model]["cost"]["total"] += cost["total"]

                # Process through pipeline
                processed = kg.process_entities_batch(entities)

                passed = sum(1 for e in processed if not e.get("blocked"))
                blocked = sum(1 for e in processed if e.get("blocked"))

                self.results[model]["total_entities"] += len(entities)
                self.results[model]["passed_entities"] += passed
                self.results[model]["blocked_entities"] += blocked

                doc_result = {
                    "rid": doc["rid"],
                    "source": doc["source"],
                    "length": doc["length"],
                    "entities_extracted": len(entities),
                    "entities_passed": passed,
                    "entities_blocked": blocked,
                    "pass_rate": (passed / len(entities) * 100) if entities else 0,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cost": cost["total"]
                }
                self.results[model]["documents"].append(doc_result)

                print(f"  ✓ Extracted: {len(entities)} | Passed: {passed} | Blocked: {blocked} | Cost: ${cost['total']:.4f}")

                # Rate limiting
                await asyncio.sleep(0.05)

            except Exception as e:
                print(f"  ❌ Exception: {str(e)}")
                self.results[model]["errors"] += 1

    @staticmethod
    def calculate_cost(model: str, input_tokens: int, output_tokens: int) -> Dict[str, float]:
        """Calculate cost for a model based on token usage."""
        # Pricing per million tokens
        pricing = {
            "gpt-4o-mini": {"input": 0.15, "output": 0.60},
            "gpt-4.1-mini": {"input": 0.40, "output": 1.60}
        }

        rates = pricing.get(model, pricing["gpt-4o-mini"])

        input_cost = (input_tokens / 1_000_000) * rates["input"]
        output_cost = (output_tokens / 1_000_000) * rates["output"]

        return {
            "input": input_cost,
            "output": output_cost,
            "total": input_cost + output_cost
        }

    def generate_report(self, output_path: str) -> None:
        """Generate A/B test comparison report."""
        mini_4o = self.results["gpt-4o-mini"]
        mini_41 = self.results["gpt-4.1-mini"]

        # Check if we have any results
        if mini_4o["total_entities"] == 0 or mini_41["total_entities"] == 0:
            print("\n❌ Error: No entities extracted. Cannot generate report.")
            return

        report = f"""# A/B Test Report: GPT-4o-mini vs GPT-4.1-mini

**Date**: {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")}
**Test Size**: {len(mini_4o['documents'])} documents per model

---

## Summary

| Metric | GPT-4o-mini | GPT-4.1-mini | Winner |
|--------|-------------|--------------|--------|
| **Total Entities** | {mini_4o['total_entities']} | {mini_41['total_entities']} | {self._winner(mini_41['total_entities'], mini_4o['total_entities'])} |
| **Passed Pipeline** | {mini_4o['passed_entities']} | {mini_41['passed_entities']} | {self._winner(mini_41['passed_entities'], mini_4o['passed_entities'])} |
| **Blocked** | {mini_4o['blocked_entities']} | {mini_41['blocked_entities']} | {self._winner(mini_4o['blocked_entities'], mini_41['blocked_entities'], lower_wins=True)} |
| **Pass Rate** | {mini_4o['passed_entities']/mini_4o['total_entities']*100:.2f}% | {mini_41['passed_entities']/mini_41['total_entities']*100:.2f}% | {self._winner(mini_41['passed_entities']/mini_41['total_entities'], mini_4o['passed_entities']/mini_4o['total_entities'])} |
| **Errors** | {mini_4o['errors']} | {mini_41['errors']} | {self._winner(mini_4o['errors'], mini_41['errors'], lower_wins=True)} |
| **Total Cost** | ${mini_4o['cost']['total']:.4f} | ${mini_41['cost']['total']:.4f} | {self._winner(mini_4o['cost']['total'], mini_41['cost']['total'], lower_wins=True)} |

---

## Cost Analysis

### GPT-4o-mini
- Input tokens: ${mini_4o['cost']['input']:.4f}
- Output tokens: ${mini_4o['cost']['output']:.4f}
- **Total**: ${mini_4o['cost']['total']:.4f}

### GPT-4.1-mini
- Input tokens: ${mini_41['cost']['input']:.4f}
- Output tokens: ${mini_41['cost']['output']:.4f}
- **Total**: ${mini_41['cost']['total']:.4f}

### Cost Difference
- GPT-4.1-mini costs **${mini_41['cost']['total'] - mini_4o['cost']['total']:.4f} more** ({(mini_41['cost']['total'] / mini_4o['cost']['total'] - 1) * 100:.1f}% increase)

### Extrapolated to Full Corpus (1,065 documents)
- GPT-4o-mini: ${mini_4o['cost']['total'] / len(mini_4o['documents']) * 1065:.2f}
- GPT-4.1-mini: ${mini_41['cost']['total'] / len(mini_41['documents']) * 1065:.2f}
- **Savings with GPT-4o-mini**: ${(mini_41['cost']['total'] - mini_4o['cost']['total']) / len(mini_4o['documents']) * 1065:.2f}

---

## Quality Analysis

### Pass Rate Distribution (GPT-4o-mini)
"""

        # Add pass rate distribution for 4o-mini
        pass_rates_4o = [doc['pass_rate'] for doc in mini_4o['documents']]
        report += f"""
- Mean: {sum(pass_rates_4o)/len(pass_rates_4o):.2f}%
- Min: {min(pass_rates_4o):.2f}%
- Max: {max(pass_rates_4o):.2f}%
- Median: {sorted(pass_rates_4o)[len(pass_rates_4o)//2]:.2f}%

### Pass Rate Distribution (GPT-4.1-mini)
"""

        # Add pass rate distribution for 4.1-mini
        pass_rates_41 = [doc['pass_rate'] for doc in mini_41['documents']]
        report += f"""
- Mean: {sum(pass_rates_41)/len(pass_rates_41):.2f}%
- Min: {min(pass_rates_41):.2f}%
- Max: {max(pass_rates_41):.2f}%
- Median: {sorted(pass_rates_41)[len(pass_rates_41)//2]:.2f}%

---

## Recommendation

"""

        # Generate recommendation
        quality_diff = (mini_41['passed_entities']/mini_41['total_entities']) - (mini_4o['passed_entities']/mini_4o['total_entities'])
        cost_ratio = mini_41['cost']['total'] / mini_4o['cost']['total']

        if quality_diff > 0.05:  # 5% quality improvement
            report += f"""✅ **Recommend GPT-4.1-mini**

GPT-4.1-mini provides **{quality_diff*100:.2f}%** better pass rate, which justifies the {(cost_ratio-1)*100:.1f}% cost increase for this use case.
"""
        elif quality_diff > 0.02:  # 2-5% improvement
            report += f"""⚖️ **Mixed Results**

GPT-4.1-mini provides **{quality_diff*100:.2f}%** better pass rate but costs **{(cost_ratio-1)*100:.1f}%** more. Consider:
- Use GPT-4.1-mini if quality is critical
- Use GPT-4o-mini if cost efficiency is priority
"""
        else:
            report += f"""✅ **Recommend GPT-4o-mini**

Quality difference is minimal (**{abs(quality_diff)*100:.2f}%**) but GPT-4o-mini costs **{(1-1/cost_ratio)*100:.1f}%** less.

The cost savings (${(mini_41['cost']['total'] - mini_4o['cost']['total']) / len(mini_4o['documents']) * 1065:.2f} for full corpus) are not justified by the marginal quality improvement.
"""

        report += "\n---\n\n## Detailed Results\n\n### Sample Documents (First 10)\n\n"
        report += "| Doc | Source | Length | GPT-4o-mini Pass Rate | GPT-4.1-mini Pass Rate | Difference |\n"
        report += "|-----|--------|--------|-----------------------|------------------------|------------|\n"

        for i in range(min(10, len(mini_4o['documents']))):
            doc_4o = mini_4o['documents'][i]
            doc_41 = mini_41['documents'][i]
            diff = doc_41['pass_rate'] - doc_4o['pass_rate']
            report += f"| {i+1} | {doc_4o['source']} | {doc_4o['length']} | {doc_4o['pass_rate']:.1f}% | {doc_41['pass_rate']:.1f}% | {diff:+.1f}% |\n"

        report += "\n---\n\n## Raw Data\n\n"
        report += f"```json\n{json.dumps(self.results, indent=2)}\n```\n"

        # Write report
        Path(output_path).write_text(report)
        print(f"\n✅ Report written to: {output_path}")

    @staticmethod
    def _winner(a: float, b: float, lower_wins: bool = False) -> str:
        """Determine winner with emoji."""
        if abs(a - b) < 0.001:
            return "🤝 Tie"
        if lower_wins:
            return "🏆 4o-mini" if b < a else "🏆 4.1-mini"
        else:
            return "🏆 4.1-mini" if a > b else "🏆 4o-mini"


async def main():
    """Run A/B test."""
    load_dotenv()

    # Configuration
    db_config = {
        "host": "localhost",
        "port": 5433,
        "database": "eliza",
        "user": "postgres",
        "password": os.getenv("POSTGRES_PASSWORD", "postgres")
    }

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("❌ Error: OPENAI_API_KEY not found in environment")
        sys.exit(1)

    # Sample size (default 50)
    sample_size = int(sys.argv[1]) if len(sys.argv) > 1 else 50

    print(f"\n{'='*60}")
    print(f"A/B Test: GPT-4o-mini vs GPT-4.1-mini")
    print(f"{'='*60}")
    print(f"Sample Size: {sample_size} documents per model")
    print(f"{'='*60}\n")

    # Initialize test
    test = ModelABTest(db_config, api_key)

    # Get sample documents
    print("📊 Sampling documents...")
    documents = test.get_sample_documents(sample_size)
    print(f"✓ Sampled {len(documents)} documents\n")

    # Test GPT-4o-mini
    await test.extract_with_model("gpt-4o-mini", documents)

    # Test GPT-4.1-mini
    await test.extract_with_model("gpt-4.1-mini", documents)

    # Generate report
    report_path = Path(__file__).parent / f"AB_TEST_REPORT_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.md"
    test.generate_report(str(report_path))

    print("\n" + "="*60)
    print("✅ A/B Test Complete!")
    print("="*60)


if __name__ == "__main__":
    asyncio.run(main())
