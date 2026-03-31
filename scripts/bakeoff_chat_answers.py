#!/usr/bin/env python3
"""
P2 Chat-Answer Provider Bakeoff — run both providers on frozen prompt packets.

Usage:
    # Generate answers from frozen packets (needs OPENAI_API_KEY + ANTHROPIC_API_KEY)
    python scripts/bakeoff_chat_answers.py

    # Custom paths
    python scripts/bakeoff_chat_answers.py \
        --packets tests/eval/results/prompt_packets.jsonl \
        --outdir tests/eval/results
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.chat_provider import OpenAIChatProvider, AnthropicChatProvider


TEMPERATURE = 0.3
MAX_TOKENS = 1024


async def run_provider(provider, packets: list[dict]) -> list[dict]:
    """Run a provider against all packets, return answers."""
    results = []
    for packet in packets:
        messages = [
            {"role": "system", "content": packet["system_prompt"]},
            {"role": "user", "content": packet["user_prompt"]},
        ]
        answer = await provider.complete(
            messages, temperature=TEMPERATURE, max_tokens=MAX_TOKENS
        )
        results.append({
            "id": packet["id"],
            "question": packet["question"],
            "answer": answer,
        })
        print(f"  {packet['id']}: {len(answer)} chars", file=sys.stderr)
    return results


def generate_comparison(packets, openai_answers, anthropic_answers, outpath: Path):
    """Generate comparison.md with side-by-side answers and scoring template."""
    oai_map = {a["id"]: a["answer"] for a in openai_answers}
    ant_map = {a["id"]: a["answer"] for a in anthropic_answers}

    lines = [
        "# P2 Chat-Answer Bakeoff — Comparison",
        "",
        f"**Packets:** {len(packets)} frozen prompt packets from Octo",
        f"**OpenAI model:** {os.getenv('CHAT_MODEL', os.getenv('CHAT_LLM_MODEL', 'gpt-4o-mini'))}",
        f"**Anthropic model:** {os.getenv('ANTHROPIC_CHAT_MODEL', 'claude-sonnet-4-20250514')}",
        f"**Temperature:** {TEMPERATURE}, **Max tokens:** {MAX_TOKENS}",
        "",
        "## Scoring Rubric (1-5 per dimension)",
        "",
        "| Dimension | Description |",
        "|-----------|-------------|",
        "| Groundedness | Does the answer use the provided context? |",
        "| Completeness | Are key entities/relationships mentioned? |",
        "| Citation | Does it reference sources/wiki links? |",
        "| Concision | Is it appropriately brief? |",
        "| Hallucination risk | Does it invent facts not in context? (5=no hallucination) |",
        "",
        "---",
        "",
    ]

    for packet in packets:
        qid = packet["id"]
        lines.extend([
            f"## {qid}: {packet['question']}",
            "",
            f"**Sources:** {len(packet.get('sources', []))} entities/docs",
            "",
            "### OpenAI",
            "",
            oai_map.get(qid, "(missing)"),
            "",
            "### Anthropic",
            "",
            ant_map.get(qid, "(missing)"),
            "",
            "### Scores",
            "",
            "| Dimension | OpenAI | Anthropic |",
            "|-----------|--------|-----------|",
            "| Groundedness | | |",
            "| Completeness | | |",
            "| Citation | | |",
            "| Concision | | |",
            "| Hallucination risk | | |",
            "| **Preferred** | | |",
            "",
            "---",
            "",
        ])

    lines.extend([
        "## Summary",
        "",
        "| Question | Preferred |",
        "|----------|-----------|",
    ])
    for packet in packets:
        lines.append(f"| {packet['id']} | |")
    lines.extend([
        "",
        "**Overall recommendation:** ",
        "",
    ])

    outpath.write_text("\n".join(lines))
    print(f"  Wrote {outpath}", file=sys.stderr)


async def main():
    parser = argparse.ArgumentParser(description="P2 chat-answer bakeoff")
    parser.add_argument("--packets", default="tests/eval/results/prompt_packets.jsonl")
    parser.add_argument("--outdir", default="tests/eval/results")
    args = parser.parse_args()

    packets_path = Path(args.packets)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    # Load packets
    packets = [json.loads(line) for line in packets_path.read_text().strip().split("\n")]
    print(f"Loaded {len(packets)} packets from {packets_path}", file=sys.stderr)

    # Init providers
    openai_key = os.environ.get("OPENAI_API_KEY")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    if not openai_key:
        print("ERROR: OPENAI_API_KEY not set", file=sys.stderr)
        sys.exit(1)
    if not anthropic_key:
        print("ERROR: ANTHROPIC_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    openai_model = os.getenv("CHAT_MODEL", os.getenv("CHAT_LLM_MODEL", "gpt-4o-mini"))
    anthropic_model = os.getenv("ANTHROPIC_CHAT_MODEL", "claude-sonnet-4-20250514")

    openai_provider = OpenAIChatProvider(api_key=openai_key, default_model=openai_model)
    anthropic_provider = AnthropicChatProvider(api_key=anthropic_key, default_model=anthropic_model)

    # Run OpenAI
    print(f"\nRunning OpenAI ({openai_model})...", file=sys.stderr)
    openai_answers = await run_provider(openai_provider, packets)
    oai_path = outdir / "answers-openai.jsonl"
    oai_path.write_text("\n".join(json.dumps(a) for a in openai_answers) + "\n")
    print(f"  Wrote {oai_path}", file=sys.stderr)

    # Run Anthropic
    print(f"\nRunning Anthropic ({anthropic_model})...", file=sys.stderr)
    anthropic_answers = await run_provider(anthropic_provider, packets)
    ant_path = outdir / "answers-anthropic.jsonl"
    ant_path.write_text("\n".join(json.dumps(a) for a in anthropic_answers) + "\n")
    print(f"  Wrote {ant_path}", file=sys.stderr)

    # Generate comparison
    print("\nGenerating comparison...", file=sys.stderr)
    generate_comparison(packets, openai_answers, anthropic_answers, outdir / "comparison.md")

    print("\nDone. Review tests/eval/results/comparison.md", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
