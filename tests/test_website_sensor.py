"""website_sensor.py — the pure parts, plus the invariants that protect the archive.

What is asserted here and why:

  * a dry-run writes NOTHING — no archive file, no git commit, no DB call. The rule is
    "--dry-run always wins over any confirm flag" (CLAUDE.md meta-learning loop).
  * a run that cannot discover the site must raise, not classify every known page as
    missing — otherwise one transient sitemap outage would retire the whole archive.
  * removal is gated by `retire_after_missing_runs` CONSECUTIVE misses, and a page that
    reappears resets the counter.
  * the archive commit happens BEFORE any ingest/retire (the "archive first" contract).
  * routing precedence: defaults < first matching rule < routing_overrides.json.
  * URL/key/path derivations are stable and filesystem-safe (no traversal from a URL).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import website_sensor as ws  # noqa: E402


def _site(tmp_path: Path, **kw) -> ws.SiteConfig:
    base = dict(site_id="example", root_url="https://www.example.org", archive_root=tmp_path / "archive",
                group_id="fieldx", default_fields=["fieldy"], default_tier="standard",
                routing=[ws.RoutingRule(match="asset:*", tier="thorough"),
                         ws.RoutingRule(match="page:*/skip-me", skip=True),
                         ws.RoutingRule(match="page:*/econ", fields=["econ"])])
    base.update(kw)
    return ws.SiteConfig(**base)


def _init_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    (root / "README.md").write_text("archive\n")
    ws.git(root, "add", "-A")
    ws.git(root, "commit", "-q", "-m", "init")


# ── pure derivations ───────────────────────────────────────────────────────────

def test_normalize_url_strips_fragment_and_trailing_slash():
    assert ws.normalize_url("HTTPS://WWW.Example.org/a/b/#frag") == "https://www.example.org/a/b"
    assert ws.normalize_url("https://www.example.org/") == "https://www.example.org/"
    assert ws.normalize_url("https://www.example.org") == "https://www.example.org/"
    assert ws.normalize_url("https://drive.google.com/open?id=ABC&usp=share") == "https://drive.google.com/open?id=ABC"


@pytest.mark.parametrize("bad", ["../../etc/passwd", "/", "..", "a/b/c", "", "%2e%2e/x"])
def test_slugify_never_yields_a_path(bad):
    s = ws.slugify(bad)
    assert "/" not in s and s not in ("", ".", "..")


def test_entry_paths_are_stable_and_safe(tmp_path):
    site = _site(tmp_path)
    f = ws.Fetched("page:www.example.org/a/../b", "page", "https://www.example.org/a/../b", "T", "# T\n", b"", ".html", 200)
    raw, md = ws.entry_paths(site, f)
    assert raw.startswith("pages/") and ".." not in raw and md.endswith(".md")
    a = ws.Fetched("asset:cdn.x/665d27ae0ecf7d66a9fbca46_Guide.pdf", "asset",
                   "https://cdn.x/665d27ae0ecf7d66a9fbca46_Guide.pdf", "Guide", "x", b"", ".pdf", 200)
    raw_a, md_a = ws.entry_paths(site, a)
    assert raw_a.startswith("assets/guide-") and raw_a.endswith(".pdf") and md_a.endswith(".md")
    assert ws.entry_paths(site, a) == (raw_a, md_a)  # stable across calls


def test_discover_documents_filters_hosts_and_parses_google(tmp_path):
    site = _site(tmp_path, asset_hosts=["cdn.allowed.com"])
    html = """
    <a href="https://cdn.allowed.com/x/report.PDF">r</a>
    <a href="https://cdn.other.com/x/evil.pdf">e</a>
    <a href="/local/paper.pdf">l</a>
    <a href="https://docs.google.com/document/d/1DOC_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/edit?usp=x">d</a>
    <a href="https://docs.google.com/spreadsheets/d/1SHEET_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/copy">s</a>
    <a href="https://drive.google.com/file/d/1DRIVE_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/view">f</a>
    <a href="https://drive.google.com/open?id=1DRIVE_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb">f2</a>
    <a href="https://docs.google.com/document/d/1DOC_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/edit">dup</a>
    """
    found = ws.discover_documents(html, "https://www.example.org/p", site)
    assert all(len(t) == 3 for t in found)                     # (kind, ident, link_text)
    assert ("gdoc", "1DOC_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "d") in found
    found = [(k, v) for k, v, _ in found]
    kinds = set(found)
    assert ("asset", "https://cdn.allowed.com/x/report.PDF") in kinds
    assert ("asset", "https://www.example.org/local/paper.pdf") in kinds
    assert not any(v.startswith("https://cdn.other.com") for _, v in found)
    assert ("gdoc", "1DOC_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa") in kinds and ("gsheet", "1SHEET_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa") in kinds
    assert ("gdrive", "1DRIVE_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa") in kinds and ("gdrive", "1DRIVE_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb") in kinds
    assert len([1 for k, v in found if v == "1DOC_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"]) == 1
    # published-to-web docs (/d/e/…) and short ids are not document ids
    assert ws.discover_documents('<a href="https://docs.google.com/document/d/e/2PACX-1vQabcdefghijklmnopqrstu/pub">p</a>'
                                 '<a href="https://docs.google.com/document/d/short/edit">s</a>', "https://www.example.org/p", site) == []


def test_discover_documents_respects_follow_google_off(tmp_path):
    site = _site(tmp_path, follow_google=False)
    html = '<a href="https://docs.google.com/document/d/1DOC_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/edit">d</a>'
    assert ws.discover_documents(html, "https://www.example.org/p", site) == []


def test_html_to_markdown_prepends_title_and_strips_chrome():
    html = """<html><head><title>Site | Page</title><script>var x=1</script></head>
    <body><nav><a href="/">Home</a></nav><h1>Real Heading</h1>
    <p>Bioregional financing facilities are community-owned institutions that channel
    capital into place-based regeneration across many watersheds and towns.</p>
    <ul><li>one item</li><li>two item</li></ul><footer>© footer</footer></body></html>"""
    md = ws.html_to_markdown(html, "https://www.example.org/p")
    assert md.startswith("# Real Heading")
    assert "community-owned institutions" in md
    assert "var x=1" not in md


def test_csv_to_markdown_drops_empty_columns():
    md = ws.csv_to_markdown(",A,,B\n,1,,2\n,,,\n", "Sheet")
    assert md.splitlines()[0] == "# Sheet"
    assert "| A | B |" in md and "| 1 | 2 |" in md


def test_drive_confirm_url_rebuilds_form():
    html = """<form id="download-form" action="https://drive.usercontent.google.com/download" method="get">
      <input type="hidden" name="id" value="ID1"><input type="hidden" name="export" value="download">
      <input type="hidden" name="confirm" value="t"><input type="hidden" name="uuid" value="U"></form>"""
    u = ws.drive_confirm_url(html)
    assert u.startswith("https://drive.usercontent.google.com/download?") and "id=ID1" in u and "confirm=t" in u
    assert ws.drive_confirm_url("<html>no form</html>") is None


# ── routing precedence ─────────────────────────────────────────────────────────

def test_route_defaults_rule_override(tmp_path):
    site = _site(tmp_path)
    d = ws.route(site, "page:www.example.org/anything", {})
    assert d == {"tier": "standard", "group_id": "fieldx", "fields": ["fieldy"], "skip": False}
    assert ws.route(site, "asset:cdn/x.pdf", {})["tier"] == "thorough"
    assert ws.route(site, "page:www.example.org/skip-me", {})["skip"] is True
    assert ws.route(site, "page:www.example.org/econ", {})["fields"] == ["fieldy", "econ"]
    ov = {"page:www.example.org/econ": {"tier": "rag", "fields": ["only"], "title": "Custom"}}
    d = ws.route(site, "page:www.example.org/econ", ov)
    assert d["tier"] == "rag" and d["fields"] == ["only"] and d["title"] == "Custom"
    # the primary group is never duplicated into fields
    assert "fieldx" not in ws.route(site, "page:x", {"page:x": {"fields": ["fieldx", "z"]}})["fields"]
    # an invalid override tier is ignored, not applied
    assert ws.route(site, "page:x", {"page:x": {"tier": "bogus"}})["tier"] == "standard"


# ── classify / manifest invariants ─────────────────────────────────────────────

def _fetched(key, md, kind="page"):
    return ws.Fetched(key, kind, "https://www.example.org/" + key.split("/", 1)[-1], "T", md, md.encode(), ".html", 200)


def test_classify_new_changed_unchanged_missing(tmp_path):
    site = _site(tmp_path)
    manifest = {"entries": {
        "page:www.example.org/a": {"sha256": ws.sha256_text("A1\n"), "status": "active"},
        "page:www.example.org/b": {"sha256": ws.sha256_text("B1\n"), "status": "active"},
        "page:www.example.org/gone": {"sha256": "x", "status": "active"},
        "page:www.example.org/old": {"sha256": "y", "status": "removed"},
    }}
    fetched = [_fetched("page:www.example.org/a", "A1\n"), _fetched("page:www.example.org/b", "B2\n"),
               _fetched("page:www.example.org/c", "C1\n"), _fetched("page:www.example.org/old", "O\n"),
               ws.Fetched("page:www.example.org/err", "page", "https://www.example.org/err", "", "", b"", ".html", 500, note="http-500")]
    d = ws.classify(site, manifest, fetched)
    assert d["unchanged"] == ["page:www.example.org/a"]
    assert d["changed"] == ["page:www.example.org/b"]
    assert set(d["new"]) == {"page:www.example.org/c", "page:www.example.org/old"}  # a removed page coming back is NEW
    assert d["unavailable"] == ["page:www.example.org/err"]
    assert d["missing"] == ["page:www.example.org/gone"]                             # already-removed is not re-missing


def test_missing_counter_increments_and_resets(tmp_path):
    site = _site(tmp_path, retire_after_missing_runs=2)
    _init_repo(site.archive_root)
    manifest = ws.load_manifest(site)
    # run 1: page a exists
    d = ws.classify(site, manifest, [_fetched("page:www.example.org/a", "A\n")])
    ws.write_snapshot(site, manifest, [_fetched("page:www.example.org/a", "A\n")], d, "r1")
    # run 2: a missing → counter 1, NOT retired
    d = ws.classify(site, manifest, [_fetched("page:www.example.org/b", "B\n")])
    ws.write_snapshot(site, manifest, [_fetched("page:www.example.org/b", "B\n")], d, "r2")
    assert manifest["entries"]["page:www.example.org/a"]["missing_runs"] == 1
    assert ws.retire_missing(site, manifest, discovery_healthy=True) == []
    # run 3: a is back → counter resets to 0
    fa = _fetched("page:www.example.org/a", "A\n")
    d = ws.classify(site, manifest, [fa])
    ws.write_snapshot(site, manifest, [fa], d, "r3")
    assert manifest["entries"]["page:www.example.org/a"]["missing_runs"] == 0
    # runs 4+5: missing twice → retired; files removed from tree; status flips
    for r in ("r4", "r5"):
        d = ws.classify(site, manifest, [_fetched("page:www.example.org/b", "B\n")])
        ws.write_snapshot(site, manifest, [_fetched("page:www.example.org/b", "B\n")], d, r)
    assert manifest["entries"]["page:www.example.org/a"]["missing_runs"] == 2
    ws.git_commit(site.archive_root, "snap")
    removed = ws.retire_missing(site, manifest, discovery_healthy=True)
    assert removed == ["page:www.example.org/a"]
    e = manifest["entries"]["page:www.example.org/a"]
    assert e["status"] == "removed" and not (site.site_dir / e["md_path"]).exists()
    # ...but the bytes are still in git history
    log = ws.git(site.archive_root, "log", "--all", "--oneline", "--", f"web/example/{e['md_path']}").stdout
    assert log.strip()


def test_retire_missing_is_inert_when_discovery_unhealthy_or_keep(tmp_path):
    site = _site(tmp_path, retire_after_missing_runs=1)
    _init_repo(site.archive_root)
    manifest = {"entries": {"page:www.example.org/a": {"status": "active", "missing_runs": 5, "md_path": "pages/a.md"}}}
    assert ws.retire_missing(site, manifest, discovery_healthy=False) == []
    site_keep = _site(tmp_path, retire_after_missing_runs=1, on_removed="keep")
    assert ws.retire_missing(site_keep, manifest, discovery_healthy=True) == []
    assert manifest["entries"]["page:www.example.org/a"]["status"] == "active"


def test_discovery_failure_raises_not_empty(tmp_path, monkeypatch):
    site = _site(tmp_path, discovery="sitemap")

    class Dead:
        def get(self, url, retries=2):
            raise RuntimeError("network down")

    with pytest.raises(RuntimeError):
        ws.discover_pages(site, Dead())


def test_write_snapshot_returns_previous_only_for_changed(tmp_path):
    site = _site(tmp_path)
    _init_repo(site.archive_root)
    manifest = ws.load_manifest(site)
    fa = _fetched("page:www.example.org/a", "A1\n")
    d = ws.classify(site, manifest, [fa])
    assert ws.write_snapshot(site, manifest, [fa], d, "r1") == {}
    manifest["entries"]["page:www.example.org/a"]["ingest"] = {"ok": True, "document_rid": "document:" + "0" * 64}
    fa2 = _fetched("page:www.example.org/a", "A2\n")
    d = ws.classify(site, manifest, [fa2])
    prev = ws.write_snapshot(site, manifest, [fa2], d, "r2")
    assert prev["page:www.example.org/a"]["ingest"]["document_rid"].startswith("document:")
    e = manifest["entries"]["page:www.example.org/a"]
    assert e["ingest"] is None and e["previous_ingest"]["ok"] is True   # new version is due for ingest
    assert (site.site_dir / e["md_path"]).read_text() == "A2\n"


# ── dry-run writes nothing; archive commits before ingest ──────────────────────

def _stub_snapshot(monkeypatch, fetched):
    monkeypatch.setattr(ws, "preflight", lambda site, mode: None)      # hermetic: no gate script / DB needed
    monkeypatch.setattr(ws, "db_unretire", lambda rid: None)
    monkeypatch.setattr(ws, "snapshot_site", lambda site, fetcher, tmp, max_docs=None: (fetched, {"sitemap_urls": 1, "crawl_urls": 0, "errors": []}))
    monkeypatch.setattr(ws, "Fetcher", lambda *a, **k: type("F", (), {"close": lambda self: None})())


def test_dry_run_writes_nothing(tmp_path, monkeypatch):
    site = _site(tmp_path)
    _init_repo(site.archive_root)
    head = ws.git(site.archive_root, "rev-parse", "HEAD").stdout
    _stub_snapshot(monkeypatch, [_fetched("page:www.example.org/a", "A\n")])
    calls = []
    monkeypatch.setattr(ws, "db_retire", lambda *a: calls.append("retire"))
    monkeypatch.setattr(ws, "db_measure", lambda *a: calls.append("measure"))
    monkeypatch.setattr(ws, "ingest_one", lambda *a: calls.append("ingest"))
    s = ws.run_site(site, "dry-run", None, None, tmp_path / "tmp")
    assert s["mode"] == "dry-run" and s["plan"][0]["key"] == "page:www.example.org/a"
    assert not site.site_dir.exists()
    assert ws.git(site.archive_root, "rev-parse", "HEAD").stdout == head
    assert calls == []


def test_archive_commit_precedes_ingest_and_ingest_records(tmp_path, monkeypatch):
    site = _site(tmp_path, ingest_concurrency=1)
    _init_repo(site.archive_root)
    _stub_snapshot(monkeypatch, [_fetched("page:www.example.org/a", "A " * 50 + "\n")])
    order = []
    real_commit = ws.git_commit

    def commit(root, msg):
        order.append("commit")
        return real_commit(root, msg)

    def ingest(site_, key, entry, decision):
        order.append("ingest")
        assert (site_.archive_root / ".git").exists()
        # the file being ingested is already committed
        assert not ws.git(site_.archive_root, "status", "--porcelain", "--", f"web/example/{entry['md_path']}").stdout.strip()
        return {"ok": True, "document_rid": "document:" + "a" * 64, "tier": decision["tier"], "fields": decision["fields"]}

    monkeypatch.setattr(ws, "git_commit", commit)
    monkeypatch.setattr(ws, "ingest_one", ingest)
    monkeypatch.setattr(ws, "db_retire", lambda *a: pytest.fail("nothing to retire on a first run"))
    s = ws.run_site(site, "ingest", None, None, tmp_path / "tmp")
    assert order[:2] == ["commit", "ingest"]
    assert s["ingest"]["ok"] == 1
    m = json.loads((site.site_dir / "manifest.json").read_text())
    assert m["entries"]["page:www.example.org/a"]["ingest"]["document_rid"].startswith("document:")
    assert (site.site_dir / "runs.jsonl").read_text().count("\n") == 1


def test_changed_document_retires_previous_only_after_success(tmp_path, monkeypatch):
    site = _site(tmp_path, ingest_concurrency=1)
    _init_repo(site.archive_root)
    old_rid, new_rid = "document:" + "1" * 64, "document:" + "2" * 64
    # seed: version 1 ingested
    _stub_snapshot(monkeypatch, [_fetched("page:www.example.org/a", "A " * 50 + "\n")])
    monkeypatch.setattr(ws, "ingest_one", lambda s, k, e, d: {"ok": True, "document_rid": old_rid})
    ws.run_site(site, "ingest", None, None, tmp_path / "tmp")
    # version 2 arrives; ingest FAILS → previous must NOT be retired
    _stub_snapshot(monkeypatch, [_fetched("page:www.example.org/a", "B " * 50 + "\n")])
    retired = []
    monkeypatch.setattr(ws, "db_retire", lambda old, new, keep=True: retired.append((old, new)) or {"old_found": True, "chunks_deleted": 3})
    monkeypatch.setattr(ws, "ingest_one", lambda s, k, e, d: {"ok": False, "error": "boom"})
    s = ws.run_site(site, "ingest", None, None, tmp_path / "tmp")
    assert s["ingest"]["failed"] == ["page:www.example.org/a"] and retired == []
    # next run: same content (unchanged vs archive) but still not ingested → retried; success retires v1
    _stub_snapshot(monkeypatch, [_fetched("page:www.example.org/a", "B " * 50 + "\n")])
    monkeypatch.setattr(ws, "ingest_one", lambda s, k, e, d: {"ok": True, "document_rid": new_rid})
    s = ws.run_site(site, "ingest", None, None, tmp_path / "tmp")
    assert s["ingest"]["ok"] == 1
    assert retired == [(old_rid, new_rid)]
    e = json.loads((site.site_dir / "manifest.json").read_text())["entries"]["page:www.example.org/a"]
    assert e["retired_previous"]["document_rid"] == old_rid and e["retired_previous"]["chunks_deleted"] == 3


def test_thin_and_skip_entries_are_not_ingested(tmp_path, monkeypatch):
    site = _site(tmp_path, min_words=40)
    _init_repo(site.archive_root)
    thin = _fetched("page:www.example.org/thin", "tiny\n")
    thin.note = "thin"
    skip = _fetched("page:www.example.org/skip-me", "S " * 60 + "\n")
    _stub_snapshot(monkeypatch, [thin, skip])
    monkeypatch.setattr(ws, "ingest_one", lambda *a: pytest.fail("must not ingest"))
    s = ws.run_site(site, "ingest", None, None, tmp_path / "tmp")
    assert s["ingest"]["attempted"] == 0 and len(s["ingest"]["skipped"]) == 2


def test_load_config_validates(tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text("sites:\n  - site_id: Bad_Id\n    root_url: https://x\n")
    with pytest.raises(ValueError):
        ws.load_config(cfg)
    cfg.write_text("sites:\n  - site_id: ok\n    root_url: https://x\n    default_tier: deep\n")
    with pytest.raises(ValueError):
        ws.load_config(cfg)
    cfg.write_text("archive_root: /tmp/a\nsites:\n  - site_id: ok\n    root_url: https://x/\n    routing:\n      - match: 'asset:*'\n        tier: thorough\n")
    sites = ws.load_config(cfg)
    assert sites[0].root_url == "https://x" and sites[0].routing[0].tier == "thorough"


def test_example_config_loads():
    sites = ws.load_config(ws.EXAMPLE_CONFIG)
    assert sites and sites[0].site_id == "example-org"


def test_keep_history_false_uses_current_dir_no_git_and_hard_retire(tmp_path, monkeypatch):
    site = _site(tmp_path, keep_history=False, ingest_concurrency=1)
    _init_repo(site.archive_root)
    head = ws.git(site.archive_root, "rev-parse", "HEAD").stdout
    assert site.site_dir == site.archive_root / "current" / "example"
    _stub_snapshot(monkeypatch, [_fetched("page:www.example.org/a", "A " * 50 + "\n")])
    monkeypatch.setattr(ws, "ingest_one", lambda s, k, e, d: {"ok": True, "document_rid": "document:" + "1" * 64})
    ws.run_site(site, "ingest", None, None, tmp_path / "tmp")
    assert ws.git(site.archive_root, "rev-parse", "HEAD").stdout == head          # no commit made
    assert (site.site_dir / "manifest.json").exists()
    # changed content → previous version retired with keep_history=False (hard delete)
    _stub_snapshot(monkeypatch, [_fetched("page:www.example.org/a", "B " * 50 + "\n")])
    calls = []
    monkeypatch.setattr(ws, "db_retire", lambda old, new, keep=True: calls.append((old, new, keep)) or {"old_found": True, "row_deleted": True, "chunks_deleted": 1})
    monkeypatch.setattr(ws, "ingest_one", lambda s, k, e, d: {"ok": True, "document_rid": "document:" + "2" * 64})
    ws.run_site(site, "ingest", None, None, tmp_path / "tmp")
    assert calls == [("document:" + "1" * 64, "document:" + "2" * 64, False)]
    assert ws.git(site.archive_root, "rev-parse", "HEAD").stdout == head


def test_keep_history_true_commits_and_soft_retires(tmp_path, monkeypatch):
    site = _site(tmp_path, keep_history=True, ingest_concurrency=1)
    _init_repo(site.archive_root)
    _stub_snapshot(monkeypatch, [_fetched("page:www.example.org/a", "A " * 50 + "\n")])
    monkeypatch.setattr(ws, "ingest_one", lambda s, k, e, d: {"ok": True, "document_rid": "document:" + "1" * 64})
    ws.run_site(site, "ingest", None, None, tmp_path / "tmp")
    _stub_snapshot(monkeypatch, [_fetched("page:www.example.org/a", "B " * 50 + "\n")])
    calls = []
    monkeypatch.setattr(ws, "db_retire", lambda old, new, keep=True: calls.append(keep) or {"old_found": True, "chunks_deleted": 1})
    monkeypatch.setattr(ws, "ingest_one", lambda s, k, e, d: {"ok": True, "document_rid": "document:" + "2" * 64})
    ws.run_site(site, "ingest", None, None, tmp_path / "tmp")
    assert calls == [True]
    assert (site.archive_root / ".gitignore").read_text().strip().endswith("current/")
    assert "ingest" in ws.git(site.archive_root, "log", "-1", "--pretty=%s").stdout


def test_xlsx_to_markdown_emits_every_tab(tmp_path):
    import openpyxl
    wb = openpyxl.Workbook()
    ws1 = wb.active; ws1.title = "Start Here"; ws1.append(["Intro", ""]); ws1.append(["one two three", ""])
    ws2 = wb.create_sheet("Rubric"); ws2.append(["Criterion", "Score"]); ws2.append(["Trust", "3"]); ws2.append(["Money | flows", "2"])
    p = tmp_path / "t.xlsx"; wb.save(p)
    md, derived = ws.xlsx_to_markdown(p.read_bytes(), "Framework")
    assert md.startswith("# Framework") and "## Tab: Start Here" in md and "## Tab: Rubric" in md
    assert "| Trust | 3 |" in md and "Money \\| flows" in md and derived == "Intro"


def test_public_host_guard_and_drive_form_host():
    assert ws.is_public_host("www.biofi.earth")
    for bad in ("localhost", "127.0.0.1", "10.0.0.5", "192.168.1.1", "169.254.169.254", "koi", "::1", "[::1]"):
        assert not ws.is_public_host(bad), bad
    html = '<form id="download-form" action="http://localhost:8351/claims/extract"><input name="id" value="X"></form>'
    assert ws.drive_confirm_url(html) is None
    html = '<form id="download-form" action="https://drive.usercontent.google.com/download"><input name="id" value="X"></form>'
    assert ws.drive_confirm_url(html).startswith("https://drive.usercontent.google.com/download?")


def test_fetcher_refuses_private_hosts():
    f = ws.Fetcher()
    try:
        with pytest.raises(RuntimeError, match="non-public"):
            f.get("http://127.0.0.1:8351/health")
    finally:
        f.close()


def test_fetch_failure_is_unavailable_not_missing(tmp_path, monkeypatch):
    site = _site(tmp_path, discovery="sitemap")
    monkeypatch.setattr(ws, "discover_pages", lambda s, f: (["https://www.example.org/a", "https://www.example.org/b"], {"sitemap_urls": 2, "crawl_urls": 0, "errors": []}))
    def fp(site_, fetcher, url):
        if url.endswith("/b"):
            raise RuntimeError("boom")
        return _fetched("page:www.example.org/a", "A " * 50 + "\n")
    monkeypatch.setattr(ws, "fetch_page", fp)
    fetched, report = ws.snapshot_site(site, object(), tmp_path)
    keys = {f.key: f for f in fetched}
    assert keys["page:www.example.org/b"].note.startswith("error:") and keys["page:www.example.org/b"].markdown == ""
    manifest = {"entries": {"page:www.example.org/b": {"sha256": "x", "status": "active", "md_path": "pages/b.md"}}}
    d = ws.classify(site, manifest, fetched)
    assert d["missing"] == [] and d["unavailable"] == ["page:www.example.org/b"]


def test_gone_counts_toward_retire_and_deferred_does_not(tmp_path):
    site = _site(tmp_path)
    manifest = {"entries": {
        "page:www.example.org/gone": {"sha256": "x", "status": "active"},
        "gdrive:1DRIVE_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa": {"sha256": "y", "status": "active"},
    }}
    fetched = [ws.Fetched("page:www.example.org/gone", "page", "https://www.example.org/gone", "", "", b"", ".html", 404, note="http-404"),
               ws.Fetched("gdrive:1DRIVE_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "gdrive", "https://drive.google.com/file/d/1DRIVE_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/view", "", "", b"", "", 0, note="deferred: max_docs")]
    d = ws.classify(site, manifest, fetched)
    assert d["missing"] == ["page:www.example.org/gone"]
    _init_repo(site.archive_root)
    ws.write_snapshot(site, manifest, fetched, d, "r1")
    assert manifest["entries"]["page:www.example.org/gone"]["missing_runs"] == 1
    assert manifest["entries"]["gdrive:1DRIVE_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"]["missing_runs"] == 0


def test_unavailable_fetch_keeps_prior_note_and_is_not_ingested(tmp_path, monkeypatch):
    site = _site(tmp_path, ingest_concurrency=1)
    _init_repo(site.archive_root)
    thin = _fetched("page:www.example.org/t", "tiny words\n"); thin.note = "thin"
    _stub_snapshot(monkeypatch, [thin])
    monkeypatch.setattr(ws, "ingest_one", lambda *a: pytest.fail("thin must not ingest"))
    ws.run_site(site, "ingest", None, None, tmp_path / "tmp")
    err = ws.Fetched("page:www.example.org/t", "page", "https://www.example.org/t", "", "", b"", ".html", 500, note="http-500")
    _stub_snapshot(monkeypatch, [err])
    ws.run_site(site, "ingest", None, None, tmp_path / "tmp")
    e = json.loads((site.site_dir / "manifest.json").read_text())["entries"]["page:www.example.org/t"]
    assert e["note"] == "thin" and e["status"] == "active" and e["last_error"] == "http-500"


def test_retire_guards_shared_rid_and_failed_retire_keeps_entry(tmp_path, monkeypatch):
    site = _site(tmp_path, retire_after_missing_runs=1)
    _init_repo(site.archive_root)
    rid = "document:" + "7" * 64
    manifest = {"entries": {
        "page:www.example.org/a": {"status": "active", "missing_runs": 1, "md_path": "pages/a.md", "sha256": "x", "ingest": {"ok": True, "document_rid": rid}},
        "page:www.example.org/b": {"status": "active", "missing_runs": 0, "md_path": "pages/b.md", "sha256": "x", "ingest": {"ok": True, "document_rid": rid}},
        "page:www.example.org/c": {"status": "active", "missing_runs": 1, "md_path": "pages/c.md", "sha256": "y", "ingest": {"ok": True, "document_rid": "document:" + "8" * 64}},
    }}
    (site.site_dir / "pages").mkdir(parents=True); (site.site_dir / "pages" / "c.md").write_text("c")
    calls = []
    def retire(old, new, keep=True):
        calls.append(old)
        raise RuntimeError("db down")
    monkeypatch.setattr(ws, "db_retire", retire)
    removed = ws.retire_missing(site, manifest, discovery_healthy=True)
    # a: rid shared with b → skipped (no DB call) but still removed from the tree
    assert "page:www.example.org/a" in removed and calls == ["document:" + "8" * 64]
    assert manifest["entries"]["page:www.example.org/a"]["retired"][0]["skipped"].startswith("rid shared")
    # c: DB retire failed → stays active, file kept, listed for retry
    assert manifest["entries"]["page:www.example.org/c"]["status"] == "active"
    assert (site.site_dir / "pages" / "c.md").exists() and manifest["retire_errors"] == ["page:www.example.org/c"]


def test_run_lock_blocks_overlap(tmp_path):
    root = tmp_path / "archive"
    with ws.RunLock(root):
        with pytest.raises(RuntimeError, match="another website-sensor run"):
            with ws.RunLock(root):
                pass


def test_config_rejects_unknown_keys_and_scalar_lists(tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text("sites:\n  - site_id: ok\n    root_url: https://x\n    tierr: standard\n")
    with pytest.raises(ValueError, match="unknown config keys"):
        ws.load_config(cfg)
    cfg.write_text("sites:\n  - site_id: ok\n    root_url: https://x\n    default_fields: bkc\n")
    with pytest.raises(ValueError, match="must be a list"):
        ws.load_config(cfg)


def test_preflight_fails_loudly_on_missing_gate(tmp_path, monkeypatch):
    site = _site(tmp_path)
    _init_repo(site.archive_root)
    monkeypatch.setattr(ws, "DEFAULT_GATE", tmp_path / "no-such-gate.py")
    monkeypatch.setattr(ws, "db_ping", lambda: None)
    with pytest.raises(RuntimeError, match="gate not found"):
        ws.preflight(site, "ingest")
    ws.preflight(site, "dry-run")            # dry-run needs no gate / DB


def test_gives_up_after_max_attempts_until_content_changes(tmp_path, monkeypatch):
    site = _site(tmp_path, ingest_concurrency=1, max_ingest_attempts=2)
    _init_repo(site.archive_root)
    doc = _fetched("page:www.example.org/a", "A " * 50 + "\n")
    calls = []
    monkeypatch.setattr(ws, "ingest_one", lambda s, k, e, d: calls.append(k) or {"ok": False, "error": "gate floor"})
    for _ in range(3):
        _stub_snapshot(monkeypatch, [doc])
        ws.run_site(site, "ingest", None, None, tmp_path / "tmp")
    assert len(calls) == 2                                    # third run skipped
    e = json.loads((site.site_dir / "manifest.json").read_text())["entries"]["page:www.example.org/a"]
    assert e["ingest"]["attempts"] == 2
    _stub_snapshot(monkeypatch, [_fetched("page:www.example.org/a", "B " * 50 + "\n")])   # content changed → retry
    ws.run_site(site, "ingest", None, None, tmp_path / "tmp")
    assert len(calls) == 3


def test_extractor_version_change_is_reported_not_silent(tmp_path, monkeypatch, caplog):
    BASE_EV = ws.EXTRACTOR_VERSION            # read it, never hard-code it
    site = _site(tmp_path, ingest_concurrency=1)
    _init_repo(site.archive_root)
    _stub_snapshot(monkeypatch, [_fetched("page:www.example.org/a", "A " * 50 + "\n")])
    monkeypatch.setattr(ws, "ingest_one", lambda s, k, e, d: {"ok": True, "document_rid": "document:" + "1" * 64})
    s1 = ws.run_site(site, "ingest", None, None, tmp_path / "tmp")
    assert s1["extractor_version"] == ws.EXTRACTOR_VERSION and "extractor_changed" not in s1
    monkeypatch.setattr(ws, "EXTRACTOR_VERSION", "9999.9")
    _stub_snapshot(monkeypatch, [_fetched("page:www.example.org/a", "A EXTRACTED DIFFERENTLY " * 20 + "\n")])
    monkeypatch.setattr(ws, "db_retire", lambda old, new, keep=True: {"old_found": True, "chunks_deleted": 1})
    monkeypatch.setattr(ws, "ingest_one", lambda s, k, e, d: {"ok": True, "document_rid": "document:" + "2" * 64})
    with caplog.at_level("WARNING"):
        s2 = ws.run_site(site, "ingest", None, None, tmp_path / "tmp")
    assert s2["extractor_changed"] == {"from": BASE_EV, "to": "9999.9", "changed": 1}
    assert "EXTRACTOR CHANGED" in caplog.text


def test_decode_mostly_text_recovers_printer_stream_and_rejects_real_binaries():
    # A legacy EPSON FX print stream: printable text wearing a binary costume.
    payload = (b"\x1d}UEPSONFXV}\x1d\r\n\x1d\r\n\x09\x10\x10!\r\n\x1dWILD CIVILIZATION:\r\n\r\n"
               + b"In March of 1990 I spent six days in a Mayan Indian Village on the edge of "
                 b"what is left of the Lacandon Rainforest in Southern Mexico.\r\n" * 3)
    text = ws.decode_mostly_text(payload)
    assert text is not None
    assert "WILD CIVILIZATION:" in text and "Lacandon Rainforest" in text
    assert "\x1d" not in text and "\x10" not in text and "\r" not in text
    # real binaries must stay rejected
    for blob in (b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 4,
                 b"%PDF-1.4" + bytes(range(256)) * 4,
                 b"PK\x03\x04" + bytes(range(256)) * 4,
                 bytes(range(256)) * 20):
        assert ws.decode_mostly_text(blob) is None
    # too short to be a document
    assert ws.decode_mostly_text(b"hello world") is None


def test_unwrap_hard_wrapped_joins_prose_and_keeps_short_lines():
    # The fixture must contain >= 5 wrap-width lines or unwrap_hard_wrapped correctly
    # declines to treat it as a wrapped document and returns it untouched.
    doc = ("WILD CIVILIZATION:\n"
           "\n"
           "In March of 1990 I spent six days in a Mayan Indian Village on the \n"
           "edge of what is left of the Lacandon Rainforest in Southern Mexico, \n"
           "the State of Chiapas. It is the northernmost such forest remaining.\n"
           "\n"
           "About fifty Lacandon Mayan families live in the village of Lacanja, \n"
           "and I stayed at the home of K'in Bor with his wife and their many \n"
           "children for the duration of that visit in the early spring season.\n"
           "\n"
           "A shortened version appeared in the jour-\n"
           "nal Conscious Choice, and the non-\n"
           "human neighbours remained interesting throughout the whole season.\n"
           "\n"
           "David Haenke\n"
           "Rt.1, Box 20\n"
           "Newburg, MO 65550\n")
    out = ws.unwrap_hard_wrapped(doc)
    # prose paragraphs are rejoined into single lines
    assert "Mayan Indian Village on the edge of what is left" in out
    # a wrap inside a hyphenated word closes up but KEEPS the hyphen (never invents a word)
    assert "jour-nal" in out and "jour- nal" not in out
    assert "non-human" in out and "nonhuman" not in out
    # deliberate short lines survive as their own lines
    for line in ("David Haenke", "Rt.1, Box 20", "Newburg, MO 65550"):
        assert line in out.split("\n")
    # a document that is not hard-wrapped is returned untouched
    plain = "Short line one.\nShort line two.\n"
    assert ws.unwrap_hard_wrapped(plain) == plain
