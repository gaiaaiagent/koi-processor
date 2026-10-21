"""Local review of cross-source discourse candidates (issue #60, increment I1).

Read-only over KOI. It lists stored candidates for a person to judge and appends
that person's annotations to a local JSONL file they name. It never writes to the
database, never admits or declines a candidate, never changes a retrieval limit
and never hides a stored candidate: pairs from one work move to their own lane
and stay listed.

What it shows comes in three kinds, kept apart:
  facts      the same-work and author mappings an operator supplies in a file
  cues       a claim's text containing an author name the mapping lists for the
             other source, or two sources from one publication feed. String
             and identifier matches only: not verified citation, restatement
             or dependence of evidence
  judgments  only what a reviewer records. Nothing here infers restatement,
             evidence dependence, support or an answer to a question.

An annotation binds to the candidate (id and key) and to the exact text and
source version shown, and names its reviewer as declared. It is appended,
never edited: a correction is a new line that supersedes an earlier one.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

from api import discourse_links as dl

QUESTION = "usefulness/v1"
QUESTION_TEXT = "Is B useful to someone studying A?"
ANSWERS = ("useful", "marginal", "not_useful", "needs_context")
ANNOTATION_SCHEMA = "koi-discourse-annotation/v1"
BATCH_SCHEMA = "koi-discourse-review-batch/v1"
ORDERS = ("source_diverse", "fused")
LANES = ("cross_source", "same_work_mapped", "same_work_suggested")
MAX_REASON_CHARS = 2000

SHARED_AUTHOR_NOTE = ("The author mapping lists an author of both sources. Shared authorship alone does not "
                      "establish that the claims share observations or evaluations.")
SAME_PUBLICATION_NOTE = ("Both source identifiers name one publication feed. That is not evidence that the "
                         "claims share observations or evaluations.")
NAME_NOTE = ("The claim text contains a name the mapping lists as an author of the other source. A name match is "
             "not verified citation, restatement or dependence.")
SUGGESTED_LANE_NOTE = ("Suggested by text similarity between the two sources (dl.same_work_signals), not by a "
                       "mapping. Unverified.")
ADMISSION_NOTE = "Admission is disabled. Binding limitations are shown so the reviewer knows what a judgment could not rely on."


class ReviewError(ValueError):
    """A review action this module refuses. Nothing has been written."""


# ---------------------------------------------------------------------------
# Reading candidates (read-only)
# ---------------------------------------------------------------------------

async def require_read_only(conn) -> None:
    if await conn.fetchval("SHOW transaction_read_only") != "on":
        raise ReviewError("review reads run only inside a READ ONLY transaction")


def _end(mv: Dict[str, Any], *, stored_repr: str, stored_bound: Optional[str]) -> Dict[str, Any]:
    ext, src = mv["extraction"], mv["source"]
    return {
        "move_id": mv["id"], "move_type": mv["move_type"], "title": mv["title"], "detail": mv["detail"],
        "status": mv["status"], "chunk_range": list(mv["chunk_range"]),
        "representation_sha256": mv["representation_sha256"],
        "representation_at_discovery": stored_repr,
        "text_changed_since_discovery": mv["representation_sha256"] != stored_repr,
        "source": {"rid": src["rid"], "title": src["title"], "url": src["source_url"],
                   "version": src["version"], "version_basis": src["version_basis"]},
        "binding": {"kind": ext["binding_kind"], "basis": ext["binding_basis"], "association": ext["association"],
                    "bound_version": ext["bound_version"], "bound_version_at_discovery": stored_bound,
                    "current": ext["current"], "limitations": list(mv["admission_blockers"])},
    }


async def candidate_rows(conn, *, run_ids: Optional[Sequence[str]] = None,
                         candidate_ids: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
    """Open candidates whose two claims the personal context may see, each with
    both claims' current text, source version and binding. SELECTs only, and
    refused outside a READ ONLY transaction."""
    await require_read_only(conn)
    rows = await conn.fetch(
        """SELECT * FROM discourse_link_candidates
            WHERE status = 'candidate'
              AND ($1::text[] IS NULL OR run_id = ANY($1::text[]))
              AND ($2::uuid[] IS NULL OR id = ANY($2::uuid[]))
            ORDER BY id""",
        list(run_ids) if run_ids else None, list(candidate_ids) if candidate_ids else None)
    ids = {str(r[k]) for r in rows for k in ("query_move_id", "target_move_id")}
    visible = await dl.visible_ids(conn, dl.personal_context(), ids)
    moves = await dl.fetch_moves(conn, visible)
    out = []
    for r in rows:
        q, t = str(r["query_move_id"]), str(r["target_move_id"])
        if q not in moves or t not in moves:
            continue
        reasons = sorted(set(
            dl._end_problems(r["query_representation_sha256"], r["query_bound_version"], moves[q], for_admission=False)
            + dl._end_problems(r["target_representation_sha256"], r["target_bound_version"], moves[t], for_admission=False)))
        out.append({
            "candidate_id": str(r["id"]), "candidate_key": r["candidate_key"], "run_id": r["run_id"],
            "method": r["method"], "discovered_by": r["discovered_by"], "times_seen": r["times_seen"],
            "scores": {k: r[k] for k in ("fused_score", "lexical_rank", "lexical_score", "semantic_rank", "semantic_score")},
            "a": _end(moves[q], stored_repr=r["query_representation_sha256"], stored_bound=r["query_bound_version"]),
            "b": _end(moves[t], stored_repr=r["target_representation_sha256"], stored_bound=r["target_bound_version"]),
            "current": not reasons, "not_current_reasons": reasons,
        })
    return out


async def suggested_same_work(conn) -> List[frozenset]:
    """Source pairs that look like two copies of one work by candidate similarity. Read-only."""
    await require_read_only(conn)
    return [frozenset((s["source_a"], s["source_b"])) for s in await dl.same_work_signals(conn, context=dl.personal_context())]


# ---------------------------------------------------------------------------
# Mappings, lanes, cues, ordering (pure)
# ---------------------------------------------------------------------------

def load_mappings(path: Optional[str]) -> Dict[str, Any]:
    """A mapping file: {"prepared_by": ..., "same_work": {work_id: [rid, ...]},
    "authors": {rid: [name, ...] or {"names": [...], "basis": ...}}}. A rid may belong to one
    work only. A source missing from "authors" has unknown authors, not "no shared author"."""
    if not path:
        return {"same_work": {}, "authors": {}, "path": None, "sha256": None, "prepared_by": None}
    raw = Path(path).read_bytes()
    data = json.loads(raw)
    rid_to_work: Dict[str, str] = {}
    for work, rids in (data.get("same_work") or {}).items():
        for rid in rids:
            if rid in rid_to_work and rid_to_work[rid] != work:
                raise ReviewError(f"{rid} is mapped to two works ({rid_to_work[rid]}, {work})")
            rid_to_work[rid] = work
    authors, author_basis = {}, {}
    for rid, entry in (data.get("authors") or {}).items():
        names = entry.get("names", []) if isinstance(entry, dict) else entry
        authors[rid] = [n.strip() for n in names if n and n.strip()]
        author_basis[rid] = entry.get("basis") if isinstance(entry, dict) else None
    return {"same_work": rid_to_work, "authors": authors, "author_basis": author_basis, "path": str(path),
            "sha256": hashlib.sha256(raw).hexdigest(), "prepared_by": data.get("prepared_by")}


def lane(row: Dict[str, Any], mappings: Dict[str, Any], suggested: Iterable[frozenset] = ()) -> str:
    a, b = row["a"]["source"]["rid"], row["b"]["source"]["rid"]
    work = mappings.get("same_work", {})
    if a in work and work.get(a) == work.get(b):
        return "same_work_mapped"
    if frozenset((a, b)) in set(suggested):
        return "same_work_suggested"
    return "cross_source"


def _publication(rid: str) -> Optional[str]:
    parts = rid.split(":")
    return parts[1] if len(parts) == 3 and parts[0] == "substack-corpus" else None


def _text(end: Dict[str, Any]) -> str:
    return " ".join(x for x in (end.get("title"), end.get("detail")) if x)


def listed_authors(row: Dict[str, Any], mappings: Dict[str, Any]) -> Dict[str, Optional[Dict[str, Any]]]:
    """Per end, the authors the mapping lists and its basis, or None when the mapping is silent."""
    out: Dict[str, Optional[Dict[str, Any]]] = {}
    for side in ("a", "b"):
        rid = row[side]["source"]["rid"]
        names = mappings.get("authors", {}).get(rid)
        out[side] = ({"names": names, "basis": mappings.get("author_basis", {}).get(rid)}
                     if names else None)
    return out


def cues(row: Dict[str, Any], mappings: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Authorship and naming cues, each labelled with its basis. None is a dependence judgment."""
    authors = mappings.get("authors", {})
    a_src, b_src = row["a"]["source"]["rid"], row["b"]["source"]["rid"]
    a_auth, b_auth = authors.get(a_src, []), authors.get(b_src, [])
    out: List[Dict[str, Any]] = []
    shared = sorted({n.casefold() for n in a_auth} & {n.casefold() for n in b_auth})
    if shared:
        out.append({"kind": "shared_author_listed", "names": shared, "basis": "author mapping file",
                    "note": SHARED_AUTHOR_NOTE})
    pa, pb = _publication(a_src), _publication(b_src)
    if pa and pa == pb:
        out.append({"kind": "same_publication", "publication": pa, "basis": "source identifiers",
                    "note": SAME_PUBLICATION_NOTE})
    for side, end, other in (("a", row["a"], b_auth), ("b", row["b"], a_auth)):
        text = _text(end)
        for name in other:
            surname = name.split()[-1]
            # Case-sensitive on purpose: a capitalised surname, so "grant" does not match "Grant".
            if re.search(rf"(?<!\w){re.escape(surname)}(?!\w)", text):
                out.append({"kind": "names_other_source_author", "side": side, "name": name, "matched": surname,
                            "basis": "string match in claim text", "note": NAME_NOTE})
    return out


def order(rows: Sequence[Dict[str, Any]], strategy: str = "source_diverse") -> List[Dict[str, Any]]:
    """fused: by fused score. source_diverse: round-robin over unordered source pairs,
    each pair's candidates by fused score, pairs in order of their best candidate.
    Every row is kept; only the order changes."""
    if strategy not in ORDERS:
        raise ReviewError(f"order must be one of {ORDERS}")
    ranked = sorted(rows, key=lambda r: (-(r["scores"]["fused_score"] or 0.0), r["candidate_id"]))
    if strategy == "fused":
        return ranked
    groups: Dict[frozenset, List[Dict[str, Any]]] = defaultdict(list)
    for r in ranked:
        groups[frozenset((r["a"]["source"]["rid"], r["b"]["source"]["rid"]))].append(r)
    queues = list(groups.values())          # insertion order = order of each pair's best candidate
    out: List[Dict[str, Any]] = []
    while any(queues):
        for qu in queues:
            if qu:
                out.append(qu.pop(0))
    return out


def queue(rows: Sequence[Dict[str, Any]], mappings: Dict[str, Any], suggested: Iterable[frozenset] = (),
          strategy: str = "source_diverse") -> Dict[str, List[Dict[str, Any]]]:
    """Rows split into lanes and ordered within each. Nothing is dropped."""
    suggested = set(suggested)
    lanes: Dict[str, List[Dict[str, Any]]] = {name: [] for name in LANES}
    for r in rows:
        name = lane(r, mappings, suggested)
        lanes[name].append({**r, "lane": name, "authors": listed_authors(r, mappings), "cues": cues(r, mappings)})
    return {name: order(items, strategy) for name, items in lanes.items()}


# ---------------------------------------------------------------------------
# Showing an item
# ---------------------------------------------------------------------------

_HIDDEN_WHEN_BLIND = ("scores", "run_id", "method", "discovered_by", "times_seen")


def view(row: Dict[str, Any], *, blind: bool = True) -> Dict[str, Any]:
    """What a reviewer sees. Blind hides retrieval scores, ranks, the run and the discoverer."""
    v = {k: v for k, v in row.items() if not (blind and k in _HIDDEN_WHEN_BLIND)}
    v["admission_note"] = ADMISSION_NOTE
    return v


def _end_lines(tag: str, end: Dict[str, Any], authors: Optional[Dict[str, Any]] = None) -> List[str]:
    src, b = end["source"], end["binding"]
    lines = [f"{tag}  [{end['move_type']}] {end['title']}"]
    if end.get("detail"):
        lines.append(f"    {end['detail']}")
    lines.append(f"    source: {src['title'] or '(no title)'}  ({src['rid']})")
    lines.append(f"    authors: {', '.join(authors['names'])} (mapping: {authors['basis'] or 'basis not stated'})"
                 if authors else "    authors: unknown (not in the mapping)")
    if end.get("chunk_range") and end["chunk_range"][0] is not None:
        lines.append(f"    extracted from source chunks {end['chunk_range'][0]}-{end['chunk_range'][1]}")
    if src.get("url"):
        lines.append(f"    url: {src['url']}")
    lines.append(f"    source version: {src['version'] or 'unknown'} ({src['version_basis']})")
    lines.append(f"    binding: {b['kind']}" + (f", {b['association']}" if b.get("association") else ""))
    for lim in b["limitations"]:
        lines.append(f"    limitation: {lim}")
    if end.get("text_changed_since_discovery"):
        lines.append("    note: claim text changed since discovery")
    return lines


def render(item: Dict[str, Any], *, position: Optional[int] = None, total: Optional[int] = None,
           blind: bool = True) -> str:
    head = f"[{position}/{total}] " if position else ""
    lines = [f"{head}candidate {item['candidate_id'][:8]}  lane: {item.get('lane', 'cross_source')}"
             + ("  (blind: retrieval scores and run hidden)" if blind else "")]
    authors = item.get("authors") or {}
    lines += _end_lines("A", item["a"], authors.get("a")) + _end_lines("B", item["b"], authors.get("b"))
    for c in item.get("cues", []):
        where = {"a": "A's text", "b": "B's text"}.get(c.get("side"), "")
        detail = (f"{where} contains '{c['matched']}' ({c['name']})" if c["kind"] == "names_other_source_author"
                  else ", ".join(c.get("names", [])) or c.get("publication", ""))
        lines.append(f"cue (unverified, {c['basis']}): {c['kind']}: {detail}. {c['note']}")
    if item.get("lane") == "same_work_suggested":
        lines.append(f"lane note: {SUGGESTED_LANE_NOTE}")
    if not blind and "scores" in item:
        s = item["scores"]
        lines.append(f"retrieval: fused {s['fused_score']:.4f}  lexical rank {s['lexical_rank']}  semantic rank {s['semantic_rank']}"
                     f"  run {item.get('run_id')}")
    lines.append(ADMISSION_NOTE)
    lines.append(f"Question: {QUESTION_TEXT}  ({' | '.join(ANSWERS)})")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Batches and annotations (local files only)
# ---------------------------------------------------------------------------

def _canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _sha(obj: Any) -> str:
    return hashlib.sha256(_canonical(obj)).hexdigest()


def _shown_end(end: Dict[str, Any]) -> Dict[str, Any]:
    keep = ("move_id", "move_type", "title", "detail", "status", "chunk_range", "representation_sha256")
    return {**{k: end[k] for k in keep}, "source": dict(end["source"]),
            "binding": {k: end["binding"][k] for k in ("kind", "association", "bound_version", "current", "limitations")}}


def make_batch(items: Sequence[Dict[str, Any]], *, database: str, blind: bool = True,
               mappings: Optional[Dict[str, Any]] = None, note: Optional[str] = None,
               created_at: Optional[str] = None) -> Dict[str, Any]:
    """A self-contained batch: the exact text and versions shown, and the mapping file behind
    its lanes and cues. No retrieval scores or run ids."""
    entries = [{"position": i, "candidate_id": it["candidate_id"], "candidate_key": it["candidate_key"],
                "lane": it.get("lane", "cross_source"), "authors": it.get("authors", {}), "cues": it.get("cues", []),
                "a": _shown_end(it["a"]), "b": _shown_end(it["b"])} for i, it in enumerate(items, start=1)]
    body = {"schema": BATCH_SCHEMA, "question": QUESTION, "question_text": QUESTION_TEXT, "answers": list(ANSWERS),
            "blind": blind, "database": database, "note": note,
            "mappings": {k: (mappings or {}).get(k) for k in ("path", "sha256", "prepared_by")}, "items": entries}
    return {**body, "batch_id": _sha(body),
            "created_at": created_at or datetime.now(timezone.utc).isoformat(timespec="microseconds")}


def binding_problems(current: Optional[Dict[str, Any]], item: Dict[str, Any]) -> List[str]:
    """Why an annotation on `item` would no longer be bound to what the reviewer saw."""
    if current is None:
        return ["candidate is no longer open or visible"]
    problems = []
    if current["candidate_key"] != item["candidate_key"]:
        problems.append("candidate key changed")
    for side in ("a", "b"):
        if current[side]["representation_sha256"] != item[side]["representation_sha256"]:
            problems.append(f"{side}: claim text changed since the batch was made")
        if current[side]["source"]["version"] != item[side]["source"]["version"]:
            problems.append(f"{side}: source version changed since the batch was made")
    return problems


def annotation(batch: Dict[str, Any], position: int, *, reviewer: str, answer: str, reason: Optional[str] = None,
               supersedes: Optional[str] = None, os_user: Optional[str] = None, tool_version: Optional[str] = None,
               created_at: Optional[str] = None) -> Dict[str, Any]:
    reviewer = (reviewer or "").strip()
    if not reviewer:
        raise ReviewError("an annotation needs a reviewer")
    if answer not in ANSWERS:
        raise ReviewError(f"answer must be one of {ANSWERS}")
    reason = (reason or "").strip() or None
    if reason and len(reason) > MAX_REASON_CHARS:
        raise ReviewError(f"reason longer than {MAX_REASON_CHARS} characters")
    items = {it["position"]: it for it in batch["items"]}
    if position not in items:
        raise ReviewError(f"batch has no item {position}")
    it = items[position]

    def shown(end):
        return {"move_id": end["move_id"], "representation_sha256": end["representation_sha256"],
                "source_rid": end["source"]["rid"], "source_version": end["source"]["version"],
                "bound_version": end["binding"]["bound_version"], "binding_kind": end["binding"]["kind"]}
    rec = {"schema": ANNOTATION_SCHEMA, "question": QUESTION, "answer": answer, "reason": reason,
           "reviewer": reviewer, "os_user": os_user,
           "created_at": created_at or datetime.now(timezone.utc).isoformat(timespec="microseconds"),
           "batch_id": batch["batch_id"], "position": position, "blind": batch["blind"], "database": batch["database"],
           "candidate": {"id": it["candidate_id"], "candidate_key": it["candidate_key"]},
           "shown": {"orientation": "A=query, B=target", "a": shown(it["a"]), "b": shown(it["b"])},
           "tool": {"name": "koi-discourse review", "version": tool_version}, "supersedes": supersedes}
    return {**rec, "annotation_id": _sha(rec)}


def read_annotations(path: str) -> List[Dict[str, Any]]:
    """Every line, each checked against its own id. A damaged line refuses the whole file."""
    p = Path(path)
    if not p.exists():
        return []
    out = []
    for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        rec = json.loads(line)
        body = {k: v for k, v in rec.items() if k != "annotation_id"}
        if rec.get("annotation_id") != _sha(body):
            raise ReviewError(f"{path}:{n}: annotation does not match its id; the file was edited or damaged")
        out.append(rec)
    return out


def append_annotation(path: str, rec: Dict[str, Any]) -> None:
    """Append one line. Refuses a damaged file, a duplicate, and a supersede of anything but
    the same reviewer's earlier annotation on the same candidate and question."""
    p = Path(path)
    if p.exists() and p.stat().st_size:
        with open(p, "rb") as f:
            f.seek(-1, os.SEEK_END)
            if f.read(1) != b"\n":
                raise ReviewError(f"{path} does not end with a newline; refusing to append to a damaged file")
    prior = read_annotations(path)
    if any(a["annotation_id"] == rec["annotation_id"] for a in prior):
        raise ReviewError("this exact annotation is already recorded")
    if rec.get("supersedes"):
        old = next((a for a in prior if a["annotation_id"] == rec["supersedes"]), None)
        if old is None or (old["reviewer"], old["candidate"]["id"], old["question"]) != (
                rec["reviewer"], rec["candidate"]["id"], rec["question"]):
            raise ReviewError("supersedes must name an earlier annotation by the same reviewer on the same "
                              "candidate and question")
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, sort_keys=True, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def latest(annotations: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Annotations no later annotation supersedes, in file order."""
    superseded = {a["supersedes"] for a in annotations if a.get("supersedes")}
    return [a for a in annotations if a["annotation_id"] not in superseded]


KEYS = {"u": "useful", "m": "marginal", "n": "not_useful", "c": "needs_context"}


def review_loop(batch: Dict[str, Any], *, reviewer: str, path: str, verify: Callable[[Dict[str, Any]], List[str]],
                ask: Callable[[str], str] = input, say: Callable[[str], None] = print,
                os_user: Optional[str] = None, tool_version: Optional[str] = None) -> int:
    """Walk a batch, skipping items this reviewer already answered in it. Returns the number recorded."""
    answered = {a["position"] for a in latest(read_annotations(path))
                if a["batch_id"] == batch["batch_id"] and a["reviewer"] == reviewer.strip()}
    total, recorded = len(batch["items"]), 0
    for it in batch["items"]:
        if it["position"] in answered:
            continue
        say(render(it, position=it["position"], total=total, blind=batch["blind"]))
        while True:
            key = ask("[u]seful  [m]arginal  [n]ot useful  needs [c]ontext  [s]kip  [q]uit: ").strip().lower()
            if key in KEYS or key in ("s", "q"):
                break
            say("Please answer u, m, n, c, s or q.")
        if key == "q":
            break
        if key == "s":
            continue
        reason = ask("Reason (optional, Enter to skip): ")
        problems = verify(it)
        if problems:
            say("Not recorded: " + "; ".join(problems))
            continue
        rec = annotation(batch, it["position"], reviewer=reviewer, answer=KEYS[key], reason=reason,
                         os_user=os_user, tool_version=tool_version)
        append_annotation(path, rec)
        recorded += 1
        say(f"Recorded {rec['annotation_id'][:12]} for item {it['position']}.")
    return recorded
