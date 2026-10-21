"""Focused tests for the local review surface (api/discourse_review.py,
scripts/discourse_review.py, scripts/koi-discourse). No database: rows are built
in the shape candidate_rows returns, and the read-only guard is tested with a
stand-in connection."""

import ast
import asyncio
import copy
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from api import discourse_review as dr  # noqa: E402

GARFIELD_DOC = "document:" + "a" * 64
GARFIELD_SUB = "substack-corpus:michaelgarfield:scenius"
LEVIN_DOC = "document:" + "b" * 64
JOHAR = "substack-corpus:indyjohar:a-drone-night-in-munich"
JOHAR2 = "substack-corpus:indyjohar:reforming-time"


def end(move_id, rid, title, *, detail=None, repr_sha="r" * 64, version="sha256:" + "v" * 64,
        kind="content_addressed_source", limitations=("content-addressed source attribution, not recorded extraction provenance",)):
    return {"move_id": move_id, "move_type": "claim", "title": title, "detail": detail, "status": "asserted",
            "chunk_range": [3, 4],
            "representation_sha256": repr_sha, "representation_at_discovery": repr_sha,
            "text_changed_since_discovery": False,
            "source": {"rid": rid, "title": f"title of {rid[:20]}", "url": None, "version": version,
                       "version_basis": "document_ingestion_log.content_hash"},
            "binding": {"kind": kind, "basis": "basis", "association": None, "bound_version": version,
                        "bound_version_at_discovery": version, "current": True, "limitations": list(limitations)}}


def row(cid, a, b, fused):
    return {"candidate_id": cid, "candidate_key": "k" + cid, "run_id": "canary:x", "method": "hybrid-rrf/v0",
            "discovered_by": "system:test", "times_seen": 1,
            "scores": {"fused_score": fused, "lexical_rank": 1, "lexical_score": 0.9, "semantic_rank": 2, "semantic_score": 0.8},
            "a": a, "b": b, "current": True, "not_current_reasons": []}


MAPPINGS = {"same_work": {GARFIELD_DOC: "garfield-scenius", GARFIELD_SUB: "garfield-scenius"},
            "authors": {LEVIN_DOC: ["Michael Levin", "Lyons"], GARFIELD_DOC: ["Michael Garfield"],
                        JOHAR: ["Indy Johar"], JOHAR2: ["Indy Johar"]},
            "author_basis": {LEVIN_DOC: "source title"},
            "path": "test", "sha256": "m" * 64, "prepared_by": "test fixture"}


def rows():
    return [
        row("c1", end("m1", GARFIELD_DOC, "Michael Levin's paper describes the economy as a collective intelligence."),
            end("m2", LEVIN_DOC, "The price system is the economy's cognitive glue."), 0.033),
        row("c2", end("m3", GARFIELD_DOC, "Industry is prokaryotic."), end("m4", GARFIELD_SUB, "Industry is prokaryotic, AI eukaryotic."), 0.032),
        row("c3", end("m5", LEVIN_DOC, "Local optimisation helps the collective."), end("m6", JOHAR, "Local optimisation hurt Munich."), 0.031),
        row("c4", end("m7", LEVIN_DOC, "Prices encode scarcity."), end("m8", JOHAR, "Taxi drivers refused long routes."), 0.030),
        row("c5", end("m9", JOHAR, "Time has three layers."), end("m10", JOHAR2, "Machine time is UTC."), 0.029),
        row("c6", end("m11", GARFIELD_DOC, "Intelligence is fractal."), end("m12", LEVIN_DOC, "All intelligence is collective."), 0.028),
    ]


def batch_of(rows_):
    lanes = dr.queue(rows_, MAPPINGS)
    items = [r for k in dr.LANES for r in lanes[k]]
    return dr.make_batch(items, database="personal_koi", mappings=MAPPINGS, created_at="2026-10-20T00:00:00+00:00")


# ---- lanes, cues, ordering ------------------------------------------------------

def test_same_work_lane_uses_mapping_and_keeps_every_candidate():
    lanes = dr.queue(rows(), MAPPINGS)
    assert [r["candidate_id"] for r in lanes["same_work_mapped"]] == ["c2"]
    assert sum(len(v) for v in lanes.values()) == len(rows())


def test_suggested_same_work_is_its_own_labelled_lane():
    lanes = dr.queue(rows(), {"same_work": {}, "authors": {}}, suggested=[frozenset((GARFIELD_DOC, GARFIELD_SUB))])
    assert [r["candidate_id"] for r in lanes["same_work_suggested"]] == ["c2"]
    assert "Unverified" in dr.render(lanes["same_work_suggested"][0])
    assert lanes["same_work_mapped"] == []


def test_mapping_file_is_loaded_with_its_hash_and_refuses_a_source_in_two_works(tmp_path):
    p = tmp_path / "m.json"
    p.write_text(json.dumps({"prepared_by": "draft", "same_work": {"w1": ["x", "y"]},
                             "authors": {"x": ["A B", " "], "y": {"names": ["C D"], "basis": "source title"}}}))
    m = dr.load_mappings(str(p))
    assert m["same_work"] == {"x": "w1", "y": "w1"} and m["prepared_by"] == "draft"
    assert m["authors"] == {"x": ["A B"], "y": ["C D"]} and m["author_basis"] == {"x": None, "y": "source title"}
    assert len(m["sha256"]) == 64
    assert dr.load_mappings(None)["same_work"] == {}
    p.write_text(json.dumps({"same_work": {"w1": ["x", "y"], "w2": ["y", "z"]}}))
    with pytest.raises(dr.ReviewError):
        dr.load_mappings(str(p))


def test_cues_are_labelled_and_never_claim_dependence():
    c1 = dr.cues(rows()[0], MAPPINGS)
    assert [c["kind"] for c in c1] == ["names_other_source_author"]
    assert c1[0]["side"] == "a" and c1[0]["matched"] == "Levin"
    assert "not verified citation, restatement or dependence" in c1[0]["note"]
    c5 = dr.cues(rows()[4], MAPPINGS)
    assert {c["kind"] for c in c5} == {"shared_author_listed", "same_publication"}
    assert all("does not establish" in c["note"] or "not evidence" in c["note"] for c in c5)
    # Only the notes mention dependence, and only to deny it. No field or value asserts it.
    fields = json.dumps([{k: v for k, v in c.items() if k != "note"} for c in c1 + c5]).lower()
    assert "depend" not in fields and "restat" not in fields and "cite" not in fields
    assert "independent" not in json.dumps([c1, c5]).lower()


def test_name_cue_is_case_sensitive_and_whole_word():
    r = rows()[0]
    r["a"]["title"] = "the Levinson effect and a levin-like idea"
    assert dr.cues(r, MAPPINGS) == []
    r["a"]["title"] = "as (Levin, 2023) argued"
    assert [c["matched"] for c in dr.cues(r, MAPPINGS)] == ["Levin"]


def test_source_diverse_order_interleaves_pairs_and_keeps_all():
    rs = [row(f"x{i}", end(f"a{i}", LEVIN_DOC, "a"), end(f"b{i}", JOHAR, "b"), 0.05 - i * 0.001) for i in range(3)]
    rs += [row(f"y{i}", end(f"c{i}", GARFIELD_DOC, "c"), end(f"d{i}", LEVIN_DOC, "d"), 0.04 - i * 0.001) for i in range(2)]
    fused = [r["candidate_id"] for r in dr.order(rs, "fused")]
    diverse = [r["candidate_id"] for r in dr.order(rs, "source_diverse")]
    assert fused == ["x0", "x1", "x2", "y0", "y1"]
    assert diverse == ["x0", "y0", "x1", "y1", "x2"]
    assert sorted(diverse) == sorted(fused)
    assert diverse == [r["candidate_id"] for r in dr.order(list(reversed(rs)), "source_diverse")]


# ---- what a reviewer sees -------------------------------------------------------

def test_blind_view_and_render_hide_scores_and_run_but_show_passages_versions_and_limits():
    item = dr.queue(rows(), MAPPINGS)["cross_source"][0]
    v = dr.view(item, blind=True)
    for hidden in ("scores", "run_id", "discovered_by", "times_seen", "method"):
        assert hidden not in v
    text = dr.render(item, position=1, total=4, blind=True)
    assert item["a"]["title"] in text and item["b"]["title"] in text
    assert "source version: sha256:" in text and "limitation: content-addressed" in text
    assert "extracted from source chunks 3-4" in text
    assert "authors: Michael Levin, Lyons (mapping: source title)" in text
    unmapped = dict(item, authors=dr.listed_authors(item, {"authors": {}}))
    assert dr.render(unmapped).count("authors: unknown (not in the mapping)") == 2
    assert "Admission is disabled" in text
    assert "fused" not in text and "canary:x" not in text
    assert "fused" in dr.render(item, blind=False)


def test_batch_carries_exact_text_and_versions_but_no_scores():
    b = batch_of(rows())
    assert b["schema"] == dr.BATCH_SCHEMA and len(b["items"]) == 6
    s = json.dumps(b)
    assert "fused_score" not in s and "canary:x" not in s and "lexical_rank" not in s
    it = b["items"][0]
    assert it["a"]["representation_sha256"] == "r" * 64 and it["a"]["source"]["version"].startswith("sha256:")
    assert b["mappings"] == {"path": "test", "sha256": "m" * 64, "prepared_by": "test fixture"}
    other = dr.make_batch([], database="personal_koi", mappings={**MAPPINGS, "sha256": "n" * 64}, created_at="x")
    assert other["batch_id"] != dr.make_batch([], database="personal_koi", mappings=MAPPINGS, created_at="x")["batch_id"]


# ---- annotations ----------------------------------------------------------------

def test_annotation_binds_candidate_text_versions_and_reviewer(tmp_path):
    b = batch_of(rows())
    rec = dr.annotation(b, 1, reviewer="darren", answer="useful", reason="  shows a citation ", os_user="u",
                        tool_version="abc", created_at="2026-10-20T01:00:00+00:00")
    it = b["items"][0]
    assert rec["candidate"] == {"id": it["candidate_id"], "candidate_key": it["candidate_key"]}
    assert rec["shown"]["a"]["representation_sha256"] == it["a"]["representation_sha256"]
    assert rec["shown"]["b"]["source_version"] == it["b"]["source"]["version"]
    assert rec["reviewer"] == "darren" and rec["reason"] == "shows a citation" and rec["question"] == dr.QUESTION
    assert rec["annotation_id"] == dr._sha({k: v for k, v in rec.items() if k != "annotation_id"})


def test_append_only_and_supersede(tmp_path):
    path = str(tmp_path / "ann.jsonl")
    b = batch_of(rows())
    first = dr.annotation(b, 1, reviewer="darren", answer="marginal", created_at="t1")
    dr.append_annotation(path, first)
    before = Path(path).read_bytes()
    second = dr.annotation(b, 1, reviewer="darren", answer="useful", supersedes=first["annotation_id"], created_at="t2")
    dr.append_annotation(path, second)
    after = Path(path).read_bytes()
    assert after.startswith(before) and after.count(b"\n") == 2
    assert [a["answer"] for a in dr.latest(dr.read_annotations(path))] == ["useful"]


def test_refusals_write_nothing(tmp_path):
    path = tmp_path / "ann.jsonl"
    b = batch_of(rows())
    with pytest.raises(dr.ReviewError):
        dr.annotation(b, 1, reviewer="  ", answer="useful")
    with pytest.raises(dr.ReviewError):
        dr.annotation(b, 1, reviewer="darren", answer="supports")
    with pytest.raises(dr.ReviewError):
        dr.annotation(b, 99, reviewer="darren", answer="useful")
    rec = dr.annotation(b, 1, reviewer="darren", answer="useful", created_at="t1")
    dr.append_annotation(str(path), rec)
    with pytest.raises(dr.ReviewError):          # exact duplicate
        dr.append_annotation(str(path), rec)
    other = dr.annotation(b, 2, reviewer="darren", answer="useful", supersedes=rec["annotation_id"], created_at="t2")
    with pytest.raises(dr.ReviewError):          # supersedes another candidate's annotation
        dr.append_annotation(str(path), other)
    assert Path(path).read_text().count("\n") == 1


def test_damaged_or_edited_file_is_refused(tmp_path):
    path = tmp_path / "ann.jsonl"
    b = batch_of(rows())
    dr.append_annotation(str(path), dr.annotation(b, 1, reviewer="darren", answer="useful", created_at="t1"))
    path.write_text(path.read_text().replace('"useful"', '"not_useful"'))
    with pytest.raises(dr.ReviewError, match="does not match its id"):
        dr.read_annotations(str(path))
    path.write_text('{"partial": ')
    with pytest.raises(dr.ReviewError, match="newline"):
        dr.append_annotation(str(path), dr.annotation(b, 2, reviewer="darren", answer="useful", created_at="t3"))


def test_binding_problems_catch_changed_text_and_versions():
    b = batch_of(rows())
    it = b["items"][0]
    current = copy.deepcopy(next(r for r in rows() if r["candidate_id"] == it["candidate_id"]))
    current["candidate_key"] = it["candidate_key"]
    assert dr.binding_problems(current, it) == []
    current["b"]["representation_sha256"] = "z" * 64
    current["a"]["source"]["version"] = "sha256:" + "w" * 64
    probs = dr.binding_problems(current, it)
    assert "b: claim text changed since the batch was made" in probs
    assert "a: source version changed since the batch was made" in probs
    assert dr.binding_problems(None, it) == ["candidate is no longer open or visible"]


def test_review_loop_records_skips_and_quits(tmp_path):
    path = str(tmp_path / "ann.jsonl")
    b = batch_of(rows())
    answers = iter(["x", "u", "relevant", "s", "c", "", "q"])
    said = []
    n = dr.review_loop(b, reviewer="darren", path=path, verify=lambda it: [], ask=lambda _: next(answers), say=said.append)
    got = dr.read_annotations(path)
    assert n == 2 and [a["answer"] for a in got] == ["useful", "needs_context"]
    assert [a["position"] for a in got] == [1, 3] and got[0]["reason"] == "relevant" and got[1]["reason"] is None
    assert any("Please answer" in s for s in said)
    blocked = iter(["u", ""])
    n2 = dr.review_loop(b, reviewer="darren", path=path, verify=lambda it: ["a: claim text changed"],
                        ask=lambda _: next(blocked, "q"), say=said.append)
    assert n2 == 0 and len(dr.read_annotations(path)) == 2


# ---- read-only and no-write guarantees -------------------------------------------

class _Conn:
    def __init__(self, ro):
        self.ro, self.queries = ro, []

    async def fetchval(self, sql, *a):
        self.queries.append(sql)
        return self.ro

    async def fetch(self, sql, *a):
        self.queries.append(sql)
        return []


def test_candidate_rows_refuses_outside_a_read_only_transaction():
    conn = _Conn("off")
    with pytest.raises(dr.ReviewError):
        asyncio.run(dr.candidate_rows(conn))
    assert conn.queries == ["SHOW transaction_read_only"]


def test_no_write_sql_in_the_review_code():
    write = re.compile(r"\b(INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM|ALTER\s+TABLE|CREATE\s+(TABLE|INDEX)|DROP\s+|TRUNCATE)\b", re.I)
    for f in (ROOT / "api" / "discourse_review.py", ROOT / "scripts" / "discourse_review.py"):
        strings = [n.value for n in ast.walk(ast.parse(f.read_text())) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        assert not [s for s in strings if write.search(s)], f


def test_wrapper_routes_review_commands_and_prints_usage():
    env = dict(os.environ, KOI_DISCOURSE_PYTHON=sys.executable, KOI_DISCOURSE_REPO_DIR=str(ROOT))
    out = subprocess.run(["bash", str(ROOT / "scripts" / "koi-discourse"), "--help"], capture_output=True, text=True, env=env)
    assert out.returncode == 0
    assert "koi-discourse annotate" in out.stdout and "Overrides:" in out.stdout and "set -euo" not in out.stdout
    bad = subprocess.run(["bash", str(ROOT / "scripts" / "koi-discourse"), "judge"], capture_output=True, text=True, env=env)
    assert bad.returncode == 2
    help_ = subprocess.run([sys.executable, "-B", str(ROOT / "scripts" / "discourse_review.py"), "annotate", "--help"],
                           capture_output=True, text=True, env=env)
    assert help_.returncode == 0 and "--reviewer" in help_.stdout


def test_wrapper_reaches_the_review_cli(tmp_path):
    """The annotations command needs no database, so it proves the route end to end."""
    path = tmp_path / "ann.jsonl"
    b = batch_of(rows())
    dr.append_annotation(str(path), dr.annotation(b, 2, reviewer="darren", answer="marginal", created_at="t1"))
    env = dict(os.environ, KOI_DISCOURSE_PYTHON=sys.executable, KOI_DISCOURSE_REPO_DIR=str(ROOT))
    out = subprocess.run(["bash", str(ROOT / "scripts" / "koi-discourse"), "annotations", "--annotations", str(path)],
                         capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stderr
    assert "marginal" in out.stdout and "1 current answer(s)" in out.stdout


def _load_cli():
    import importlib.util
    spec = importlib.util.spec_from_file_location("discourse_review_cli", ROOT / "scripts" / "discourse_review.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_cli_batch_records_the_mapping_and_refuses_to_overwrite(tmp_path, monkeypatch):
    """Drives cmd_batch with the database read stubbed: the batch must carry the mapping file's hash."""
    cli = _load_cli()
    mp = tmp_path / "mapping.json"
    mp.write_text(json.dumps({"prepared_by": "test", "same_work": {}, "authors": {LEVIN_DOC: ["Michael Levin"]}}))
    lanes = dr.queue(rows(), dr.load_mappings(str(mp)))
    picked = [lanes["cross_source"][0]]

    async def fake_readonly(dsn, fn):
        return "personal_koi", picked
    monkeypatch.setattr(cli, "_readonly", fake_readonly)
    out = tmp_path / "batch.json"
    args = type("A", (), {"out": str(out), "mappings": str(mp), "candidate_id": None, "from_queue": 1,
                          "order": "source_diverse", "note": "n", "dsn": "unused"})()
    cli.cmd_batch(args)
    b = json.loads(out.read_text())
    assert b["mappings"]["sha256"] == dr.load_mappings(str(mp))["sha256"] and b["mappings"]["prepared_by"] == "test"
    assert b["items"][0]["authors"]["b"]["names"] == ["Michael Levin"]
    with pytest.raises(SystemExit, match="refusing to overwrite"):
        cli.cmd_batch(args)
