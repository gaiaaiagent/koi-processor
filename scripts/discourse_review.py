#!/usr/bin/env python3
"""Local review of cross-source discourse candidates (issue #60, increment I1).

Every database read runs in one READ ONLY transaction. Nothing here writes to the
database, admits, declines or hides a candidate, or changes a retrieval limit.
The only files written are the ones you name: a batch file (batch) and an
append-only annotation file (annotate, review).

  queue        open candidates in lanes (cross_source, same_work_mapped,
               same_work_suggested), each ordered source_diverse (default) or by
               fused score. Text by default, --format json for JSON.
  batch        a self-contained review batch: the exact text and versions shown,
               no retrieval scores or run ids. From --candidate-id, or the first
               --from-queue N of the cross_source lane.
  show         render a batch (blind). --verify re-checks it against the database.
  annotate     record one answer to "Is B useful to someone studying A?" for one
               batch item: useful | marginal | not_useful | needs_context, with an
               optional reason. Refused if the candidate's text or source version
               changed since the batch was made.
  review       walk a batch interactively and record answers as annotate does.
  annotations  summarise an annotation file (current answers per item).

Mappings (--mappings, or KOI_DISCOURSE_REVIEW_MAPPINGS): an operator JSON file
{"same_work": {work_id: [rid, ...]}, "authors": {rid: [name, ...]}}. Mapped facts
drive the same-work lane and authorship cues. Cues are never dependence judgments.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import asyncpg

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from api import discourse_review as dr  # noqa: E402

DSN = "postgresql://darrenzal:@localhost:5432/personal_koi?application_name=koi-discourse-review"


async def _readonly(dsn: str, fn):
    conn = await asyncpg.connect(dsn)
    try:
        async with conn.transaction(readonly=True):
            return await fn(conn)
    finally:
        await conn.close()


def _tool_version() -> Optional[str]:
    try:
        out = subprocess.run(["git", "-C", str(REPO_ROOT), "describe", "--always", "--dirty"],
                             capture_output=True, text=True, timeout=10)
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _mappings(path: Optional[str]) -> Dict[str, Any]:
    return dr.load_mappings(path or os.environ.get("KOI_DISCOURSE_REVIEW_MAPPINGS"))


async def _queue(conn, args, mappings):
    rows = await dr.candidate_rows(conn, run_ids=args.run_id or None)
    suggested = await dr.suggested_same_work(conn)
    return await conn.fetchval("SELECT current_database()"), dr.queue(rows, mappings, suggested, args.order)


def cmd_queue(args) -> None:
    mappings = _mappings(args.mappings)
    database, lanes = asyncio.run(_readonly(args.dsn, lambda c: _queue(c, args, mappings)))
    wanted = dr.LANES if args.lane == "all" else (args.lane,)
    blind = not args.show_scores
    if args.format == "json":
        print(json.dumps({"database": database, "read_only": True, "order": args.order, "mappings": mappings["path"],
                          "label": "candidates (unreviewed): similarity proposals, not judgments",
                          "counts": {k: len(v) for k, v in lanes.items()},
                          "lanes": {k: [dr.view(r, blind=blind) for r in lanes[k][:args.limit]] for k in wanted}},
                         indent=1, default=str))
        return
    print(f"{database}  order={args.order}  " + "  ".join(f"{k}={len(v)}" for k, v in lanes.items()))
    for k in wanted:
        print(f"\n== lane {k}: {len(lanes[k])} candidates" + (f" (showing {args.limit})" if args.limit < len(lanes[k]) else ""))
        for i, r in enumerate(lanes[k][:args.limit], start=1):
            print("\n" + dr.render(r, position=i, total=len(lanes[k]), blind=blind))


def cmd_batch(args) -> None:
    out = Path(args.out)
    if out.exists():
        raise SystemExit(f"refusing to overwrite {out}")
    mappings = _mappings(args.mappings)

    async def body(conn):
        rows = await dr.candidate_rows(conn, candidate_ids=args.candidate_id or None)
        suggested = await dr.suggested_same_work(conn)
        lanes = dr.queue(rows, mappings, suggested, args.order)
        if args.candidate_id:
            by_id = {r["candidate_id"]: r for k in dr.LANES for r in lanes[k]}
            missing = [c for c in args.candidate_id if c not in by_id]
            if missing:
                raise SystemExit(f"not open or not visible: {', '.join(missing)}")
            items = [by_id[c] for c in args.candidate_id]
        else:
            items = lanes["cross_source"][:args.from_queue]
        return await conn.fetchval("SELECT current_database()"), items
    database, items = asyncio.run(_readonly(args.dsn, body))
    batch = dr.make_batch(items, database=database, blind=True, mappings=mappings, note=args.note)
    out.write_text(json.dumps(batch, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {out}: {len(batch['items'])} items, batch_id {batch['batch_id'][:12]}")


def _load_batch(path: str) -> Dict[str, Any]:
    batch = json.loads(Path(path).read_text())
    if batch.get("schema") != dr.BATCH_SCHEMA:
        raise SystemExit(f"{path} is not a {dr.BATCH_SCHEMA} file")
    return batch


def _verify(dsn: str, items: List[Dict[str, Any]]) -> Dict[int, List[str]]:
    async def body(conn):
        rows = {r["candidate_id"]: r for r in await dr.candidate_rows(conn, candidate_ids=[i["candidate_id"] for i in items])}
        return {i["position"]: dr.binding_problems(rows.get(i["candidate_id"]), i) for i in items}
    return asyncio.run(_readonly(dsn, body))


def cmd_show(args) -> None:
    batch = _load_batch(args.batch)
    items = [i for i in batch["items"] if args.item in (None, i["position"])]
    problems = _verify(args.dsn, items) if args.verify else {}
    print(f"batch {batch['batch_id'][:12]}  {len(batch['items'])} items  question: {batch['question_text']}")
    for it in items:
        print("\n" + dr.render(it, position=it["position"], total=len(batch["items"]), blind=batch["blind"]))
        if args.verify:
            print("verify: " + ("; ".join(problems[it["position"]]) or "unchanged since the batch was made"))


def cmd_annotate(args) -> None:
    batch = _load_batch(args.batch)
    item = next((i for i in batch["items"] if i["position"] == args.item), None)
    if item is None:
        raise SystemExit(f"batch has no item {args.item}")
    problems = _verify(args.dsn, [item])[item["position"]]
    if problems:
        raise SystemExit("not recorded: " + "; ".join(problems))
    rec = dr.annotation(batch, args.item, reviewer=args.reviewer, answer=args.answer, reason=args.reason,
                        supersedes=args.supersedes, os_user=getpass.getuser(), tool_version=_tool_version())
    dr.append_annotation(args.annotations, rec)
    print(f"recorded {rec['annotation_id'][:12]}: item {args.item} = {args.answer}")


def cmd_review(args) -> None:
    batch = _load_batch(args.batch)
    n = dr.review_loop(batch, reviewer=args.reviewer, path=args.annotations,
                       verify=lambda it: _verify(args.dsn, [it])[it["position"]],
                       os_user=getpass.getuser(), tool_version=_tool_version())
    print(f"{n} answer(s) recorded in {args.annotations}")


def cmd_annotations(args) -> None:
    current = dr.latest(dr.read_annotations(args.annotations))
    if args.batch:
        current = [a for a in current if a["batch_id"] == _load_batch(args.batch)["batch_id"]]
    for a in current:
        print(f"{a['position']:>3}  {a['answer']:<13} {a['reviewer']:<12} {a['candidate']['id'][:8]}  "
              f"{a['created_at']}  {a['reason'] or ''}")
    print(f"{len(current)} current answer(s)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser("queue")
    q.add_argument("--dsn", default=DSN)
    q.add_argument("--run-id", action="append")
    q.add_argument("--order", choices=dr.ORDERS, default="source_diverse")
    q.add_argument("--lane", choices=("all",) + dr.LANES, default="all")
    q.add_argument("--limit", type=int, default=10)
    q.add_argument("--mappings")
    q.add_argument("--show-scores", action="store_true", help="not blind: show retrieval scores and run")
    q.add_argument("--format", choices=("text", "json"), default="text")
    b = sub.add_parser("batch")
    b.add_argument("--dsn", default=DSN)
    b.add_argument("--candidate-id", action="append")
    b.add_argument("--from-queue", type=int, default=10)
    b.add_argument("--order", choices=dr.ORDERS, default="source_diverse")
    b.add_argument("--mappings")
    b.add_argument("--note")
    b.add_argument("--out", required=True)
    s = sub.add_parser("show")
    s.add_argument("--dsn", default=DSN)
    s.add_argument("--batch", required=True)
    s.add_argument("--item", type=int)
    s.add_argument("--verify", action="store_true")
    a = sub.add_parser("annotate")
    a.add_argument("--dsn", default=DSN)
    a.add_argument("--batch", required=True)
    a.add_argument("--item", type=int, required=True)
    a.add_argument("--answer", choices=dr.ANSWERS, required=True)
    a.add_argument("--reason")
    a.add_argument("--reviewer", required=True)
    a.add_argument("--annotations", required=True)
    a.add_argument("--supersedes")
    r = sub.add_parser("review")
    r.add_argument("--dsn", default=DSN)
    r.add_argument("--batch", required=True)
    r.add_argument("--reviewer", required=True)
    r.add_argument("--annotations", required=True)
    n = sub.add_parser("annotations")
    n.add_argument("--dsn", default=DSN)
    n.add_argument("--annotations", required=True)
    n.add_argument("--batch")
    args = ap.parse_args()
    try:
        {"queue": cmd_queue, "batch": cmd_batch, "show": cmd_show, "annotate": cmd_annotate,
         "review": cmd_review, "annotations": cmd_annotations}[args.cmd](args)
    except dr.ReviewError as e:
        raise SystemExit(f"refused: {e}")


if __name__ == "__main__":
    main()
