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
    <a href="https://docs.google.com/document/d/DOC_1/edit?usp=x">d</a>
    <a href="https://docs.google.com/spreadsheets/d/SHEET_1/copy">s</a>
    <a href="https://drive.google.com/file/d/DRIVE_1/view">f</a>
    <a href="https://drive.google.com/open?id=DRIVE_2">f2</a>
    <a href="https://docs.google.com/document/d/DOC_1/edit">dup</a>
    """
    found = ws.discover_documents(html, "https://www.example.org/p", site)
    assert all(len(t) == 3 for t in found)                     # (kind, ident, link_text)
    assert ("gdoc", "DOC_1", "d") in found
    found = [(k, v) for k, v, _ in found]
    kinds = set(found)
    assert ("asset", "https://cdn.allowed.com/x/report.PDF") in kinds
    assert ("asset", "https://www.example.org/local/paper.pdf") in kinds
    assert not any(v.startswith("https://cdn.other.com") for _, v in found)
    assert ("gdoc", "DOC_1") in kinds and ("gsheet", "SHEET_1") in kinds
    assert ("gdrive", "DRIVE_1") in kinds and ("gdrive", "DRIVE_2") in kinds
    assert len([1 for k, v in found if v == "DOC_1"]) == 1


def test_discover_documents_respects_follow_google_off(tmp_path):
    site = _site(tmp_path, follow_google=False)
    html = '<a href="https://docs.google.com/document/d/DOC_1/edit">d</a>'
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
