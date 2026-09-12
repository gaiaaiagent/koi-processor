#!/usr/bin/env python3
"""Website sensor for personal-koi — git-versioned site archive → deep ingest of what changed.

For each configured site (config/website_sources.yaml):

  1. DISCOVER   sitemap.xml (+ same-host link crawl) → the set of live pages.
  2. SNAPSHOT   fetch every page → pages/<slug>.html + extracted pages/<slug>.md; follow
                links to documents (PDF on the site / allow-listed CDN hosts, public Google
                Docs / Sheets / Drive files) → assets/, gdocs/. All under a local-only git
                repo (~/Documents/koi-source-archive by default).
  3. DIFF       manifest.json (previous run) vs this run → new / changed / unchanged /
                missing. A document is keyed by its URL; its content identity is
                sha256(markdown) — the same hash `ingest_document.py` uses for document_rid.
  4. COMMIT     `git commit` the snapshot BEFORE any database mutation. This is the
                versioned archive: any earlier version is `git log -- <path>` away.
  5. INGEST     new + changed documents through scripts/ingest_document.py (rag | standard
                | thorough; group_id + multi-field membership from routing rules and
                routing_overrides.json), N at a time, each verified by the document-ingest
                gate and a direct DB measurement (chunks > 0, 0 null-embeds).
  6. RETIRE     a changed document's previous version, and documents missing for
                `retire_after_missing_runs` consecutive runs: koi_memories.superseded_at is
                stamped and the old RAG chunks are dropped. Facts, claims and entity links
                are KEPT with provenance to the archived version. Nothing is hard-deleted;
                the bytes stay in git.

Modes (a dry-run always wins):
    --dry-run       discover + fetch into memory, print the plan; write NOTHING (no archive,
                    no git, no DB).
    --archive-only  snapshot + commit; no ingest, no retire.
    (default)       snapshot + commit + ingest + retire.

Usage (source config/personal.env first, or use scripts/run_website_sensor.sh):
    python scripts/website_sensor.py --dry-run
    python scripts/website_sensor.py --site biofi-earth --archive-only
    python scripts/website_sensor.py --site biofi-earth --max-ingest 3     # smoke test
    python scripts/website_sensor.py --status
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import fcntl
import fnmatch
import hashlib
import html as html_lib
import io
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import ipaddress
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse, urlunparse, parse_qs

import httpx
import yaml
from bs4 import BeautifulSoup

try:  # main-content extraction; falls back to a bs4 converter when absent
    import trafilatura  # type: ignore
except Exception:  # pragma: no cover
    trafilatura = None

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("website-sensor")

REPO_ROOT = Path(__file__).resolve().parent.parent
POSTGRES_URL = os.getenv("POSTGRES_URL", "postgresql://darrenzal:@localhost:5432/personal_koi")
DEFAULT_ARCHIVE_ROOT = "~/Documents/koi-source-archive"
DEFAULT_CONFIG = REPO_ROOT / "config" / "website_sources.yaml"
EXAMPLE_CONFIG = REPO_ROOT / "config" / "website_sources.example.yaml"
DEFAULT_GATE = Path(os.getenv(
    "DOC_INGEST_GATE",
    "~/.claude/local/dw-plugin/scripts/document-ingest-gate/verify_doc_ingest.py",
)).expanduser()
UA = os.getenv(
    "WEBSITE_SENSOR_UA",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36 personal-koi-website-sensor",
)
DOC_EXTS = (".pdf", ".docx", ".pptx", ".xlsx", ".csv", ".txt", ".md")
PDF_EXTS = (".pdf",)
VALID_TIERS = ("rag", "standard", "thorough")
# Content identity is sha256(extracted markdown), so a change to ANY extractor re-classifies
# every unchanged page as "changed" and re-ingests the whole site. Bump this whenever the
# extraction of existing content changes; the run then says so out loud instead of the diff
# silently reading as "the site changed overnight".
EXTRACTOR_VERSION = "2026-09-11.4"   # walk-preferred HTML, reading-order PDF, xlsx sheets
FETCH_TIMEOUT = 60.0
POLITE_DELAY = 0.5

# Google Docs / Drive link shapes. Only PUBLIC exports are fetched (no auth is ever sent);
# a 401/403 is recorded as `private` and never retried with credentials.
GOOGLE_DOC_RE = re.compile(r"https?://docs\.google\.com/document/d/(?!e/)([\w-]{20,})")
GOOGLE_SHEET_RE = re.compile(r"https?://docs\.google\.com/spreadsheets/d/(?!e/)([\w-]{20,})")
GOOGLE_SLIDES_RE = re.compile(r"https?://docs\.google\.com/presentation/d/(?!e/)([\w-]{20,})")
GOOGLE_DRIVE_RE = re.compile(r"https?://drive\.google\.com/(?:file/d/([\w-]{20,})|open\?id=([\w-]{20,}))")
GOOGLE_HOST_RE = re.compile(r"(^|\.)(google\.com|googleusercontent\.com)$")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def short_hash(s: str, n: int = 10) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:n]


def slugify(s: str, max_len: int = 80) -> str:
    """Filesystem-safe slug. Never produces path separators, dots-only names or empties."""
    s = html_lib.unescape(s)
    s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()
    s = s[:max_len].strip("-")
    return s or "untitled"


def normalize_url(url: str) -> str:
    """Canonical form used as the manifest key: scheme+host lowercased, no fragment,
    no trailing slash (except root), query kept only for google `open?id=` links."""
    p = urlparse(url.strip())
    scheme = (p.scheme or "https").lower()
    netloc = p.netloc.lower()
    path = re.sub(r"/{2,}", "/", p.path) or "/"
    if len(path) > 1:
        path = path.rstrip("/")
    query = ""
    if netloc.endswith("drive.google.com") and "id=" in p.query:
        query = "id=" + (parse_qs(p.query).get("id", [""])[0])
    return urlunparse((scheme, netloc, path, "", query, ""))


# ── Config ─────────────────────────────────────────────────────────────────────

@dataclass
class RoutingRule:
    match: str                      # fnmatch pattern over the entry key, e.g. "asset:*", "page:*/what-is-a-bff"
    tier: Optional[str] = None
    fields: List[str] = field(default_factory=list)
    group_id: Optional[str] = None
    skip: bool = False


@dataclass
class SiteConfig:
    site_id: str
    root_url: str
    archive_root: Path
    discovery: str = "both"                 # sitemap | crawl | both
    sitemap_url: Optional[str] = None
    max_pages: int = 200
    include: List[str] = field(default_factory=list)   # regexes on the URL path; empty = all
    exclude: List[str] = field(default_factory=list)
    asset_hosts: List[str] = field(default_factory=list)  # extra hosts whose linked documents are archived
    follow_google: bool = True
    group_id: str = "personal"
    default_fields: List[str] = field(default_factory=list)
    default_tier: str = "standard"
    routing: List[RoutingRule] = field(default_factory=list)
    min_words: int = 40
    on_removed: str = "retire"              # retire | keep
    retire_after_missing_runs: int = 2
    ingest_concurrency: int = 2
    ingest_timeout: int = 3600
    render: str = "http"                    # http only in v1 (Playwright rendering not implemented)
    keep_history: bool = True               # True: git-versioned archive + supersede old versions in the DB.
                                            # False: overwrite in place (no git) + delete old versions outright.
    pdf_layout: bool = False                # pdftotext reading-order (default) vs -layout. -layout interleaves
                                            # multi-column designed PDFs; use it only for single-column print books.
    max_download_mb: int = 200              # per-response size cap (disk + git protection)
    max_ingest_attempts: int = 3            # stop retrying an unchanged document after this many failed runs

    @property
    def host(self) -> str:
        return urlparse(self.root_url).netloc.lower()

    @property
    def site_dir(self) -> Path:
        # history-off sites live under current/ which the archive repo ignores (see ensure_archive_repo)
        return self.archive_root / ("web" if self.keep_history else "current") / self.site_id


def load_config(path: Path) -> List[SiteConfig]:
    if not path.exists():
        raise FileNotFoundError(f"config not found: {path} (copy {EXAMPLE_CONFIG.name} to start)")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    archive_default = Path(raw.get("archive_root", DEFAULT_ARCHIVE_ROOT)).expanduser()
    sites: List[SiteConfig] = []
    known = {f.name for f in SiteConfig.__dataclass_fields__.values()}
    list_keys = ("include", "exclude", "asset_hosts", "default_fields", "routing")
    for s in raw.get("sites", []):
        if not s.get("site_id") or not s.get("root_url"):
            raise ValueError("every site needs site_id + root_url")
        unknown = set(s) - known
        if unknown:
            raise ValueError(f"{s['site_id']}: unknown config keys {sorted(unknown)}")
        for lk in list_keys:
            if lk in s and s[lk] is not None and not isinstance(s[lk], list):
                raise ValueError(f"{s['site_id']}: {lk} must be a list")
        for rx in list(s.get("include") or []) + list(s.get("exclude") or []):
            re.compile(rx)                                    # raise here, not mid-run
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", s["site_id"]):
            raise ValueError(f"site_id must be a lowercase slug: {s['site_id']!r}")
        tier = s.get("default_tier", "standard")
        if tier not in VALID_TIERS:
            raise ValueError(f"{s['site_id']}: default_tier {tier!r} not in {VALID_TIERS}")
        rules = []
        for r in s.get("routing", []) or []:
            if r.get("tier") and r["tier"] not in VALID_TIERS:
                raise ValueError(f"{s['site_id']}: routing tier {r['tier']!r} invalid")
            rules.append(RoutingRule(
                match=r["match"], tier=r.get("tier"), fields=list(r.get("fields") or []),
                group_id=r.get("group_id"), skip=bool(r.get("skip", False)),
            ))
        on_removed = s.get("on_removed", "retire")
        if on_removed not in ("retire", "keep"):
            raise ValueError(f"{s['site_id']}: on_removed must be retire|keep")
        sites.append(SiteConfig(
            site_id=s["site_id"],
            root_url=s["root_url"].rstrip("/"),
            archive_root=Path(s.get("archive_root", archive_default)).expanduser(),
            discovery=s.get("discovery", "both"),
            sitemap_url=s.get("sitemap_url"),
            max_pages=int(s.get("max_pages", 200)),
            include=list(s.get("include") or []),
            exclude=list(s.get("exclude") or []),
            asset_hosts=[h.lower() for h in (s.get("asset_hosts") or [])],
            follow_google=bool(s.get("follow_google", True)),
            group_id=s.get("group_id", "personal"),
            default_fields=list(s.get("default_fields") or []),
            default_tier=tier,
            routing=rules,
            min_words=int(s.get("min_words", 40)),
            on_removed=on_removed,
            retire_after_missing_runs=int(s.get("retire_after_missing_runs", 2)),
            ingest_concurrency=int(s.get("ingest_concurrency", 2)),
            ingest_timeout=int(s.get("ingest_timeout", 3600)),
            render=s.get("render", "http"),
            keep_history=bool(s.get("keep_history", True)),
            pdf_layout=bool(s.get("pdf_layout", False)),
            max_download_mb=int(s.get("max_download_mb", 200)),
            max_ingest_attempts=int(s.get("max_ingest_attempts", 3)),
        ))
    return sites


# ── Fetching ───────────────────────────────────────────────────────────────────

def is_public_host(host: str) -> bool:
    """Refuse loopback / private / link-local targets and bare hostnames: the sensor follows
    links from untrusted pages and must never be turned into a probe of localhost:8351."""
    host = (host or "").strip("[]").lower()
    if not host or host == "localhost" or ("." not in host and ":" not in host):
        return False
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_global
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return True                       # unresolvable → let the fetch fail on its own
    return all(ipaddress.ip_address(i[4][0]).is_global for i in infos)


class TooLarge(RuntimeError):
    pass


class Fetcher:
    def __init__(self, timeout: float = FETCH_TIMEOUT, max_bytes: int = 200 * 1024 * 1024):
        self.client = httpx.Client(
            headers={"User-Agent": UA, "Accept": "*/*"},
            follow_redirects=True, timeout=timeout,
        )
        self.max_bytes = max_bytes
        self._last = 0.0

    def close(self):
        self.client.close()

    def get(self, url: str, retries: int = 2) -> httpx.Response:
        if not is_public_host(urlparse(url).hostname or ""):
            raise RuntimeError(f"refusing non-public host: {url}")
        wait = time.monotonic() - self._last
        if wait < POLITE_DELAY:
            time.sleep(POLITE_DELAY - wait)
        last_exc: Optional[Exception] = None
        for attempt in range(retries + 1):
            try:
                with self.client.stream("GET", url) as resp:
                    self._last = time.monotonic()
                    if not is_public_host(resp.url.host or ""):
                        raise RuntimeError(f"redirected to non-public host: {url} → {resp.url}")
                    if resp.status_code >= 500 and attempt < retries:
                        time.sleep(2 * (attempt + 1))
                        continue
                    declared = int(resp.headers.get("content-length") or 0)
                    if declared > self.max_bytes:
                        raise TooLarge(f"{url}: content-length {declared} > cap {self.max_bytes}")
                    buf = bytearray()
                    for chunk in resp.iter_bytes():
                        buf += chunk
                        if len(buf) > self.max_bytes:
                            raise TooLarge(f"{url}: body exceeded cap {self.max_bytes}")
                    resp._content = bytes(buf)          # materialize so .text/.content work after close
                    return resp
            except TooLarge:
                raise
            except (httpx.TransportError, httpx.TimeoutException) as e:
                last_exc = e
                time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"fetch failed after {retries + 1} attempts: {url}: {last_exc}")


def parse_sitemap(xml: str, base: str) -> List[str]:
    """Return page URLs from a urlset, recursing into sitemap indexes is the caller's job."""
    soup = BeautifulSoup(xml, "xml")
    return [loc.get_text(strip=True) for loc in soup.find_all("loc")]


def discover_pages(site: SiteConfig, fetcher: Fetcher) -> Tuple[List[str], Dict[str, Any]]:
    """Return (page URLs, discovery report). Raises if NO discovery method succeeded —
    a run that cannot see the site must never be allowed to mark everything as missing."""
    report: Dict[str, Any] = {"sitemap_urls": 0, "crawl_urls": 0, "errors": []}
    found: List[str] = []
    seen: Set[str] = set()

    def accept(u: str) -> bool:
        p = urlparse(u)
        if p.netloc.lower() != site.host or p.scheme not in ("http", "https"):
            return False
        path = p.path or "/"
        if any(path.lower().endswith(ext) for ext in DOC_EXTS):
            return False
        if site.include and not any(re.search(rx, path) for rx in site.include):
            return False
        if any(re.search(rx, path) for rx in site.exclude):
            return False
        return True

    def add(u: str):
        n = normalize_url(u)
        if n not in seen and accept(n):
            seen.add(n)
            found.append(n)

    if site.discovery in ("sitemap", "both"):
        sm_url = site.sitemap_url or f"{site.root_url}/sitemap.xml"
        try:
            queue = [sm_url]
            visited: Set[str] = set()
            while queue and len(visited) < 50:
                u = queue.pop(0)
                if u in visited:
                    continue
                visited.add(u)
                resp = fetcher.get(u)
                if resp.status_code != 200:
                    report["errors"].append(f"sitemap {u}: HTTP {resp.status_code}")
                    continue
                locs = parse_sitemap(resp.text, u)
                for loc in locs:
                    if loc.lower().endswith(".xml"):
                        if urlparse(loc).netloc.lower() == site.host:
                            queue.append(loc)
                    else:
                        add(loc)
                        report["sitemap_urls"] += 1
        except Exception as e:
            report["errors"].append(f"sitemap: {e}")

    if site.discovery in ("crawl", "both"):
        add(site.root_url)
        queue = list(found)
        visited: Set[str] = set()
        while queue and len(found) < site.max_pages:
            u = queue.pop(0)
            if u in visited:
                continue
            visited.add(u)
            try:
                resp = fetcher.get(u)
            except Exception as e:
                report["errors"].append(f"crawl {u}: {e}")
                continue
            if resp.status_code != 200 or "html" not in resp.headers.get("content-type", ""):
                continue
            soup = BeautifulSoup(resp.text, "lxml")
            for a in soup.find_all("a", href=True):
                link = normalize_url(urljoin(u, a["href"]))
                if accept(link) and link not in seen:
                    add(link)
                    queue.append(link)
                    report["crawl_urls"] += 1

    if not found:
        raise RuntimeError(f"{site.site_id}: discovery found no pages ({report['errors']})")
    return found[: site.max_pages], report


# ── Content extraction ─────────────────────────────────────────────────────────

def html_title(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for sel in ("h1", "title"):
        el = soup.find(sel)
        if el and el.get_text(strip=True):
            return re.sub(r"\s+", " ", el.get_text(strip=True))
    return ""


BLOCK_TAGS = {"p", "div", "section", "article", "main", "aside", "li", "ul", "ol", "h1", "h2", "h3", "h4",
              "h5", "h6", "blockquote", "td", "th", "tr", "table", "figcaption", "dt", "dd", "pre", "details",
              "summary", "label"}
CHROME_TAGS = ["script", "style", "noscript", "svg", "iframe", "nav", "footer", "header", "template",
               "input", "select", "textarea", "button", "option"]   # NOT <form>: Webflow wraps content lists in one


def _bs4_to_markdown(html: str) -> str:
    """Structure-preserving fallback converter. Walks block elements and emits each block's
    own text as a paragraph / heading / list item — including text that lives in bare
    <div>s (Webflow, Squarespace) which a p/li-only pass drops entirely."""
    from bs4 import NavigableString, Tag
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(CHROME_TAGS):
        tag.decompose()
    for tag in soup.find_all(class_=re.compile(r"(^|\s)(w-nav|navbar|nav-menu|footer|cookie|w-embed)(\s|$)", re.I)):
        tag.decompose()
    root = soup.body or soup
    lines: List[str] = []
    buf: List[str] = []

    def flush(tag_name: str = "p"):
        text = re.sub(r"[ \t]+", " ", " ".join(buf)).strip()
        buf.clear()
        if not text:
            return
        one_line = re.sub(r"\s+", " ", text)
        if tag_name in ("h1", "h2", "h3", "h4", "h5", "h6"):
            lines.append(f"\n{'#' * int(tag_name[1])} {one_line}\n")
        elif tag_name == "li":
            lines.append(f"- {one_line}")
        elif tag_name == "blockquote":
            lines.append(f"> {text}")
        else:
            lines.append(text + "\n")

    def walk(el: Tag, ctx: str):
        for child in el.children:
            if isinstance(child, NavigableString):
                if child.strip():
                    buf.append(str(child))
            elif isinstance(child, Tag):
                if child.name == "br":
                    buf.append("\n")
                elif child.name in BLOCK_TAGS:
                    flush(ctx)
                    walk(child, child.name if child.name in ("h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote") else "p")
                    flush(child.name if child.name in ("h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote") else "p")
                elif child.find(BLOCK_TAGS):          # inline wrapper (e.g. an <a> card) holding blocks
                    flush(ctx)
                    walk(child, ctx)
                    flush(ctx)
                else:
                    buf.append(child.get_text(" ", strip=True))

    walk(root, "p")
    flush("p")
    # Webflow renders the same nav/CTA twice (desktop + mobile) — drop exact consecutive repeats.
    out: List[str] = []
    for ln in lines:
        if not out or out[-1] != ln:
            out.append(ln)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip() + "\n"


def html_to_markdown(html: str, url: str) -> str:
    """Main-content extraction (trafilatura) unless the structural walk recovers materially
    more text — measured on biofi.earth, each extractor loses whole sections the other keeps
    (trafilatura: 0 words on a div-based glossary; the walk: link-card lists the readability
    heuristic discards)."""
    traf = ""
    if trafilatura is not None:
        try:
            traf = trafilatura.extract(
                html, url=url, output_format="markdown", include_links=True,
                include_tables=True, include_images=False, favor_recall=True,
            ) or ""
        except Exception as e:  # pragma: no cover
            logger.warning("trafilatura failed for %s: %s", url, e)
    walk = _bs4_to_markdown(html)
    # The walk keeps headings (trafilatura's markdown dropped every heading on biofi.earth);
    # prefer it unless trafilatura recovered materially more text.
    md = walk if word_count(walk) >= 0.9 * word_count(traf) else traf
    title = html_title(html)
    md = md.strip() + "\n"
    if title and not md.lstrip().startswith("#"):
        md = f"# {title}\n\n{md}"
    return md


def pdf_to_markdown(pdf_path: Path, layout: bool = False) -> Tuple[str, int]:
    """pdftotext (never pymupdf4llm — see the ingest-source skill: ligature corruption).
    Reading-order mode by default: `-layout` interleaves the columns of designed multi-column
    PDFs (the BioFi DAF guide came out as three columns spliced line-by-line). Use
    layout=True only for single-column print books. Returns (markdown, page_count) where
    page_count counts ALL pages, so an image-only deck reports its true size."""
    if not shutil.which("pdftotext"):
        raise RuntimeError("pdftotext not on PATH (brew install poppler)")
    cmd = ["pdftotext"] + (["-layout"] if layout else []) + ["-enc", "UTF-8", str(pdf_path), "-"]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if out.returncode != 0:
        raise RuntimeError(f"pdftotext failed: {out.stderr.strip()[:200]}")
    pages = out.stdout.split("\f")
    page_count = max(len(pages) - 1, 1) if out.stdout else 0
    text = "\n\n".join(p.rstrip() for p in pages if p.strip())
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip() + "\n", page_count


def csv_to_markdown(csv_text: str, title: str = "") -> str:
    rows = [r for r in csv.reader(io.StringIO(csv_text))]
    rows = [[c.strip() for c in r] for r in rows]
    rows = [r for r in rows if any(r)]
    if not rows:
        return ""
    # drop columns that are empty in every row
    width = max(len(r) for r in rows)
    keep = [i for i in range(width) if any(i < len(r) and r[i] for r in rows)]
    rows = [[(r[i] if i < len(r) else "") for i in keep] for r in rows]
    lines = [f"# {title}", ""] if title else []
    for i, r in enumerate(rows):
        cells = [c.replace("|", "\\|").replace("\n", " ") for c in r]
        lines.append("| " + " | ".join(cells) + " |")
        if i == 0:
            lines.append("|" + "---|" * len(cells))
    return "\n".join(lines) + "\n"


def xlsx_to_markdown(data: bytes, title: str = "") -> Tuple[str, str]:
    """Every worksheet of an .xlsx as one markdown document (a `## Tab:` section + table per
    sheet). Google's CSV export returns only the FIRST tab — the Self-Assessment rubric on
    biofi.earth kept 2,885 of its 3,600 words on tab 2. Returns (markdown, derived_title)."""
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    sections: List[str] = []
    derived = ""
    for ws in wb.worksheets:
        rows = [[re.sub(r"\s+", " ", str(c)).strip() if c is not None else "" for c in r]
                for r in ws.iter_rows(values_only=True)]
        rows = [r for r in rows if any(r)]
        if not rows:
            continue
        if not derived:
            derived = next((c for r in rows for c in r if c), "")[:200]
        width = max(len(r) for r in rows)
        keep = [i for i in range(width) if any(i < len(r) and r[i] for r in rows)]
        rows = [[(r[i] if i < len(r) else "") for i in keep] for r in rows]
        lines = [f"## Tab: {ws.title.strip()}", ""]
        for i, r in enumerate(rows):
            cells = [c.replace("|", "\\|") for c in r]
            lines.append("| " + " | ".join(cells) + " |")
            if i == 0:
                lines.append("|" + "---|" * len(cells))
        sections.append("\n".join(lines))
    md = (f"# {title or derived}\n\n" if (title or derived) else "") + "\n\n".join(sections) + "\n"
    return md, derived


PRINTABLE_TEXT_RATIO = 0.95


def decode_mostly_text(data: bytes) -> Optional[str]:
    """Return readable text for a payload that is text wearing a binary costume, else None.

    biofi.earth links David Haenke's "Wild Civilization" as a Drive file that is neither a
    PDF nor UTF-8: it is a legacy EPSON FX *printer control stream* — 21,570 bytes of which
    99.8% are printable, the essay's 3,301 words interleaved with a handful of escape codes.
    The old rule keyed on file extension and content-type and filed it `binary-unsupported`,
    discarding a complete essay because of its wrapper. A payload that decodes to >=95%
    printable characters IS text; decode it and strip the control bytes rather than
    declaring it unreadable.
    """
    if not data or data[:4] in (b"\x89PNG", b"%PDF", b"PK\x03\x04", b"\xff\xd8\xff\xe0", b"GIF8"):
        return None
    for enc in ("utf-8", "cp437", "latin-1"):
        try:
            text = data.decode(enc)
        except UnicodeDecodeError:
            continue
        printable = sum(1 for ch in text if ch.isprintable() or ch in "\t\n\r")
        if printable / max(len(text), 1) < PRINTABLE_TEXT_RATIO:
            continue
        text = re.sub(r"^\x1d\}U[A-Z]+\}\x1d", "", text)          # printer init banner
        text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", text)      # control bytes, keep \t \n
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = "\n".join(ln for ln in text.split("\n")
                         if re.sub(r"[!^~\s]", "", ln) or not ln.strip())  # drop emphasis-code-only lines
        text = unwrap_hard_wrapped(text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        return text + "\n" if len(text.split()) >= 20 else None
    return None


def unwrap_hard_wrapped(text: str, min_len: int = 55) -> str:
    """Rejoin lines broken by fixed-width wrapping, leaving deliberate short lines alone.

    A 1991 print file wraps every sentence at ~66 columns, so the naive text carries a
    newline mid-clause roughly every twelve words. That damages chunk embeddings and gives
    the extractor fragments instead of sentences. A line is a continuation only if it is
    near the wrap width (>= min_len); genuinely short lines — headings, the author's
    address block, list items — keep their break.
    """
    lines = text.split("\n")
    if sum(1 for ln in lines if len(ln.rstrip()) >= min_len) < 5:
        return text                                   # not a wrapped document; leave it alone
    out: List[str] = []
    buf = ""
    for ln in lines:
        stripped = ln.rstrip()
        if not stripped.strip():                      # blank line ends a paragraph
            if buf:
                out.append(buf.strip()); buf = ""
            out.append("")
            continue
        # A line ending in a hyphen is a split word by definition, whatever its length.
        if buf and (len(buf) >= min_len or buf.rstrip().endswith("-")):
            prev = buf.rstrip()
            # A wrap can split a hyphenated word ("fanta-\nsies", "non-\nhuman"). Close the
            # gap but KEEP the hyphen. Deciding whether to also drop it needs a dictionary:
            # /usr/share/dict/words is the 1934 Webster list with no inflections, so it
            # rejects "fantasies" and "corporations" while happily joining "non-human" into
            # "nonhuman" — i.e. it corrupts the author's text in exactly the cases it is
            # confident about. Tested: 10/12 correct, and the 2 failures are silent. Keeping
            # the hyphen is wrong for no word and mildly ugly for a few.
            buf = prev + stripped.lstrip() if prev.endswith("-") else prev + " " + stripped.lstrip()
        else:
            if buf:
                out.append(buf.strip())
            buf = stripped
        if len(stripped) < min_len and not stripped.endswith("-"):   # short line ends the paragraph
            out.append(buf.strip()); buf = ""
    if buf:
        out.append(buf.strip())
    return "\n".join(out)


def word_count(text: str) -> int:
    return len(re.findall(r"\w+", text))


# ── Link discovery on a page ───────────────────────────────────────────────────

def discover_documents(html: str, page_url: str, site: SiteConfig) -> List[Tuple[str, str, str]]:
    """Return [(kind, ident, link_text)] for documents linked from a page.
    kind ∈ asset|gdoc|gsheet|gslides|gdrive; link_text is the anchor's visible text (the
    human title for slide decks whose first text line is noise)."""
    soup = BeautifulSoup(html, "lxml")
    out: List[Tuple[str, str, str]] = []
    seen: Set[str] = set()
    for a in soup.find_all("a", href=True):
        raw = urljoin(page_url, a["href"].strip())
        link_text = re.sub(r"\s+", " ", a.get_text(" ", strip=True))[:200]
        p = urlparse(raw)
        if p.scheme not in ("http", "https"):
            continue
        host = p.netloc.lower()
        path_l = p.path.lower()
        kind: Optional[str] = None
        key_url = raw
        if any(path_l.endswith(ext) for ext in DOC_EXTS) and (host == site.host or host in site.asset_hosts):
            kind, key_url = "asset", normalize_url(raw)
        elif site.follow_google:
            m = GOOGLE_DOC_RE.match(raw)
            if m:
                kind, key_url = "gdoc", m.group(1)
            else:
                m = GOOGLE_SHEET_RE.match(raw)
                if m:
                    kind, key_url = "gsheet", m.group(1)
                else:
                    m = GOOGLE_SLIDES_RE.match(raw)
                    if m:
                        kind, key_url = "gslides", m.group(1)
                    else:
                        m = GOOGLE_DRIVE_RE.match(raw)
                        if m:
                            kind, key_url = "gdrive", (m.group(1) or m.group(2))
        if kind and (kind, key_url) not in seen:
            seen.add((kind, key_url))
            out.append((kind, key_url, link_text))
    return out


def google_export_url(kind: str, gid: str) -> str:
    return {
        "gdoc": f"https://docs.google.com/document/d/{gid}/export?format=txt",
        "gsheet": f"https://docs.google.com/spreadsheets/d/{gid}/export?format=xlsx",   # ALL tabs (csv = first tab only)
        "gslides": f"https://docs.google.com/presentation/d/{gid}/export/pdf",
        "gdrive": f"https://drive.google.com/uc?export=download&id={gid}",
    }[kind]


def google_canonical_url(kind: str, gid: str) -> str:
    return {
        "gdoc": f"https://docs.google.com/document/d/{gid}/edit",
        "gsheet": f"https://docs.google.com/spreadsheets/d/{gid}/edit",
        "gslides": f"https://docs.google.com/presentation/d/{gid}/edit",
        "gdrive": f"https://drive.google.com/file/d/{gid}/view",
    }[kind]


def drive_confirm_url(html: str) -> Optional[str]:
    """Large Drive files answer with a 'can't scan for viruses' form; rebuild its GET."""
    soup = BeautifulSoup(html, "lxml")
    form = soup.find("form", id="download-form") or soup.find("form")
    if not form or not form.get("action"):
        return None
    params = {i["name"]: i.get("value", "") for i in form.find_all("input", attrs={"name": True})}
    params.setdefault("confirm", "t")
    from urllib.parse import urlencode
    action = urljoin("https://drive.google.com/", form["action"])
    if not GOOGLE_HOST_RE.search(urlparse(action).netloc.lower()):
        return None                                     # never follow a form off Google
    return action + "?" + urlencode(params)


# ── Manifest ───────────────────────────────────────────────────────────────────

def manifest_path(site: SiteConfig) -> Path:
    return site.site_dir / "manifest.json"


def load_manifest(site: SiteConfig) -> Dict[str, Any]:
    p = manifest_path(site)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"site_id": site.site_id, "root_url": site.root_url, "entries": {}, "runs": 0}


def save_manifest(site: SiteConfig, manifest: Dict[str, Any]) -> None:
    manifest["updated_at"] = utcnow()
    manifest["entries"] = dict(sorted(manifest["entries"].items()))
    manifest_path(site).write_text(json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=False) + "\n",
                                   encoding="utf-8")


def load_overrides(site: SiteConfig) -> Dict[str, Any]:
    p = site.site_dir / "routing_overrides.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8")).get("entries", {})
    return {}


def route(site: SiteConfig, key: str, overrides: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve tier / group_id / fields / skip for an entry key: defaults ← first matching
    rule ← routing_overrides.json (which wins)."""
    decision = {"tier": site.default_tier, "group_id": site.group_id,
                "fields": list(site.default_fields), "skip": False}
    for rule in site.routing:
        if fnmatch.fnmatch(key, rule.match):
            if rule.tier:
                decision["tier"] = rule.tier
            if rule.group_id:
                decision["group_id"] = rule.group_id
            for f in rule.fields:
                if f not in decision["fields"]:
                    decision["fields"].append(f)
            decision["skip"] = rule.skip
            break
    ov = overrides.get(key) or {}
    if ov.get("tier") in VALID_TIERS:
        decision["tier"] = ov["tier"]
    if ov.get("group_id"):
        decision["group_id"] = ov["group_id"]
    if ov.get("fields") is not None:
        decision["fields"] = list(ov["fields"])
    if "skip" in ov:
        decision["skip"] = bool(ov["skip"])
    if ov.get("title"):
        decision["title"] = ov["title"]
    decision["fields"] = [f for f in decision["fields"] if f and f != decision["group_id"]]
    return decision


# ── Snapshot ───────────────────────────────────────────────────────────────────

@dataclass
class Fetched:
    key: str
    kind: str                 # page | asset | gdoc | gsheet | gslides | gdrive
    source_url: str
    title: str
    markdown: str             # "" when not extractable
    raw: bytes                # html or binary
    raw_ext: str              # .html | .pdf | .csv | .txt
    http_status: int
    note: str = ""            # private | gone | thin | binary-unsupported | ...
    pages: int = 0


def safe_rel(site: SiteConfig, sub: str, name: str, ext: str) -> str:
    """Relative path inside the site dir; slug + short hash keeps names unique and safe."""
    return f"{sub}/{name}{ext}"


def fetch_page(site: SiteConfig, fetcher: Fetcher, url: str) -> Fetched:
    key = "page:" + url.split("://", 1)[1]
    resp = fetcher.get(url)
    if resp.status_code != 200:
        return Fetched(key, "page", url, "", "", b"", ".html", resp.status_code, note=f"http-{resp.status_code}")
    html = resp.text
    md = html_to_markdown(html, url)
    title = html_title(html) or urlparse(url).path.strip("/") or site.host
    note = "thin" if word_count(md) < site.min_words else ""
    return Fetched(key, "page", url, title, md, html.encode("utf-8"), ".html", 200, note=note)


def fetch_document(site: SiteConfig, fetcher: Fetcher, kind: str, ident: str, tmp_dir: Path,
                   link_text: str = "") -> Fetched:
    """Fetch a linked document. `ident` is a URL for assets, a Google id otherwise.
    `link_text` (the anchor text on the linking page) wins as the title for PDFs/decks."""
    if kind == "asset":
        url, key = ident, "asset:" + ident.split("://", 1)[1]
    else:
        url, key = google_export_url(kind, ident), f"{kind}:{ident}"
    canonical = ident if kind == "asset" else google_canonical_url(kind, ident)
    resp = fetcher.get(url)
    status = resp.status_code
    if status in (401, 403):
        return Fetched(key, kind, canonical, "", "", b"", "", status, note="private")
    if status in (404, 410):
        return Fetched(key, kind, canonical, "", "", b"", "", status, note="gone")
    if status != 200:
        return Fetched(key, kind, canonical, "", "", b"", "", status, note=f"http-{status}")
    ctype = resp.headers.get("content-type", "").lower()
    data = resp.content
    # Drive's virus-scan interstitial for large files
    if kind == "gdrive" and "text/html" in ctype:
        confirm = drive_confirm_url(resp.text)
        if not confirm:
            return Fetched(key, kind, canonical, "", "", b"", "", status, note="drive-interstitial-unparsed")
        resp = fetcher.get(confirm)
        ctype = resp.headers.get("content-type", "").lower()
        data = resp.content
        if resp.status_code != 200 or "text/html" in ctype:
            return Fetched(key, kind, canonical, "", "", b"", "", resp.status_code, note="drive-confirm-failed")

    if kind == "gdoc":
        text = data.decode("utf-8", errors="replace").lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
        title = next((ln.strip() for ln in text.splitlines() if ln.strip()), f"Google Doc {ident}")[:200]
        md = f"# {title}\n\n{text.strip()}\n" if not text.lstrip().startswith("#") else text
        note = "thin" if word_count(md) < site.min_words else ""
        return Fetched(key, kind, canonical, title, md, data, ".txt", 200, note=note)
    if kind == "gsheet":
        if data[:2] == b"PK":                       # xlsx (zip) — every tab
            md, derived = xlsx_to_markdown(data, "")
            title = (link_text if len(link_text.split()) >= 2 else derived) or f"Google Sheet {ident}"
            md, _ = xlsx_to_markdown(data, title[:200])
            note = "thin" if word_count(md) < site.min_words else ""
            return Fetched(key, kind, canonical, title[:200], md, data, ".xlsx", 200, note=note)
        text = data.decode("utf-8", errors="replace").lstrip("\ufeff")   # csv fallback (first tab)
        rows = [r for r in csv.reader(io.StringIO(text))]
        title = next((c.strip() for r in rows for c in r if c.strip()), f"Google Sheet {ident}")[:200]
        md = csv_to_markdown(text, title)
        note = "thin" if word_count(md) < site.min_words else ""
        return Fetched(key, kind, canonical, title, md, data, ".csv", 200, note=note)

    # asset / gslides / gdrive → expect a PDF (or another document type we keep but cannot convert)
    is_pdf = data[:5] == b"%PDF-" or "pdf" in ctype
    if not is_pdf:
        ext = Path(urlparse(url).path).suffix.lower() or ".bin"
        if ext in (".txt", ".md"):
            text = data.decode("utf-8", errors="replace")
            title = next((ln.strip("# ").strip() for ln in text.splitlines() if ln.strip()), key)[:200]
            return Fetched(key, kind, canonical, title, text, data, ext, 200)
        if ext == ".csv":
            text = data.decode("utf-8", errors="replace")
            return Fetched(key, kind, canonical, key, csv_to_markdown(text), data, ext, 200)
        text = decode_mostly_text(data)
        if text is not None:
            title = (link_text if len(link_text.split()) >= 2 else
                     next((ln.strip() for ln in text.splitlines() if len(ln.strip()) > 3), key))[:200]
            md = text if text.lstrip().startswith("#") else f"# {title}\n\n{text}"
            note = "thin" if word_count(md) < site.min_words else ""
            return Fetched(key, kind, canonical, title, md, data, ".txt", 200, note=note)
        return Fetched(key, kind, canonical, "", "", data, ext if ext != ".bin" else ".bin", 200,
                       note="binary-unsupported")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_pdf = tmp_dir / (short_hash(key) + ".pdf")
    tmp_pdf.write_bytes(data)
    try:
        md, pages = pdf_to_markdown(tmp_pdf, layout=site.pdf_layout)
    finally:
        tmp_pdf.unlink(missing_ok=True)
    if len(link_text.split()) >= 2:
        title = link_text
    elif kind == "asset":
        from urllib.parse import unquote
        base = unquote(Path(urlparse(url).path).name)
        title = re.sub(r"^[0-9a-f]{20,}_", "", base)          # strip Webflow's upload-id prefix
        title = re.sub(r"\.pdf$", "", title, flags=re.I).replace("_", " ").strip() or base
    else:
        title = next((ln.strip() for ln in md.splitlines() if len(ln.strip()) > 3), f"{kind} {ident}")[:200]
    note = "thin" if word_count(md) < site.min_words else ""
    return Fetched(key, kind, canonical, title, md, data, ".pdf", 200, note=note, pages=pages)


def entry_paths(site: SiteConfig, f: Fetched) -> Tuple[str, str]:
    """(raw_rel, md_rel) for a fetched item; stable across runs for the same key."""
    h = short_hash(f.key)
    if f.kind == "page":
        path = urlparse(f.source_url).path.strip("/") or "index"
        name = f"{slugify(path.replace('/', '-'))[:60]}-{h[:6]}"
        return f"pages/{name}.html", f"pages/{name}.md"
    if f.kind == "asset":
        base = slugify(re.sub(r"^[0-9a-f]{20,}_", "", Path(urlparse(f.source_url).path).stem))
        return f"assets/{base}-{h}{f.raw_ext or '.bin'}", f"assets/{base}-{h}.md"
    base = slugify(f.title)[:60] if f.title else f.kind
    return f"gdocs/{base}-{h}{f.raw_ext or '.bin'}", f"gdocs/{base}-{h}.md"


def snapshot_site(site: SiteConfig, fetcher: Fetcher, tmp_dir: Path, max_docs: Optional[int] = None
                  ) -> Tuple[List[Fetched], Dict[str, Any]]:
    pages, report = discover_pages(site, fetcher)
    logger.info("%s: discovered %d pages (sitemap=%d crawl=%d)", site.site_id, len(pages),
                report["sitemap_urls"], report["crawl_urls"])
    fetched: List[Fetched] = []
    docs: Dict[Tuple[str, str], str] = {}     # (kind, ident) → anchor text on the first page that linked it
    for url in pages:
        try:
            f = fetch_page(site, fetcher, url)
        except Exception as e:
            report["errors"].append(f"page {url}: {e}")
            f = Fetched("page:" + url.split("://", 1)[1], "page", url, "", "", b"", ".html", 0, note=f"error: {str(e)[:120]}")
        fetched.append(f)
        if f.http_status == 200:
            for kind, ident, link_text in discover_documents(f.raw.decode("utf-8", errors="replace"), url, site):
                if (kind, ident) not in docs or (not docs[(kind, ident)] and link_text):
                    docs[(kind, ident)] = link_text
    logger.info("%s: %d linked documents discovered", site.site_id, len(docs))
    for i, ((kind, ident), link_text) in enumerate(docs.items()):
        key = ("asset:" + ident.split("://", 1)[1]) if kind == "asset" else f"{kind}:{ident}"
        canonical = ident if kind == "asset" else google_canonical_url(kind, ident)
        if max_docs is not None and i >= max_docs:
            # deferred, NOT missing — a smoke-test cap must never start the retire clock
            fetched.append(Fetched(key, kind, canonical, "", "", b"", "", 0, note="deferred: max_docs"))
            continue
        try:
            f = fetch_document(site, fetcher, kind, ident, tmp_dir, link_text=link_text)
            logger.info("  %s %s → %s%s", kind, ident[:60], f.title[:60] or "-", f" [{f.note}]" if f.note else "")
        except Exception as e:
            report["errors"].append(f"{kind} {ident}: {e}")
            f = Fetched(key, kind, canonical, "", "", b"", "", 0, note=f"error: {str(e)[:120]}")
        fetched.append(f)
    if max_docs is not None and len(docs) > max_docs:
        report["errors"].append(f"max_docs={max_docs}: {len(docs) - max_docs} documents deferred")
    return fetched, report


# ── Diff + write + git ─────────────────────────────────────────────────────────

def classify(site: SiteConfig, manifest: Dict[str, Any], fetched: List[Fetched]) -> Dict[str, List[str]]:
    entries = manifest["entries"]
    now_keys = {f.key for f in fetched}
    new, changed, unchanged, unavailable = [], [], [], []
    for f in fetched:
        prev = entries.get(f.key)
        if not f.markdown:
            unavailable.append(f.key)
            continue
        h = sha256_text(f.markdown)
        if prev is None or prev.get("status") == "removed":
            new.append(f.key)
        elif prev.get("sha256") != h:
            changed.append(f.key)
        else:
            unchanged.append(f.key)
    gone = {f.key for f in fetched if not f.markdown and f.http_status in (404, 410)}
    missing = [k for k, e in entries.items()
               if (k not in now_keys or k in gone) and e.get("status") != "removed" and e.get("sha256")]
    return {"new": new, "changed": changed, "unchanged": unchanged, "unavailable": unavailable, "missing": missing}


def write_snapshot(site: SiteConfig, manifest: Dict[str, Any], fetched: List[Fetched], diff: Dict[str, List[str]],
                   run_id: str) -> Dict[str, Any]:
    """Write files + update manifest entries (in memory). Returns {key: previous_entry} for
    changed keys so the ingest stage can retire the previous version after success."""
    site.site_dir.mkdir(parents=True, exist_ok=True)
    entries = manifest["entries"]
    previous: Dict[str, Any] = {}
    now = utcnow()
    for f in fetched:
        prev = entries.get(f.key) or {}
        if not f.markdown:
            e = dict(prev)
            e.update({"kind": f.kind, "source_url": f.source_url, "last_attempt": now,
                      "last_http_status": f.http_status, "last_error": f.note})
            if not prev.get("sha256"):                      # never archived: unavailable is its whole state
                e.update({"status": "unavailable", "note": f.note, "http_status": f.http_status, "missing_runs": 0})
            elif f.key not in diff["missing"]:               # archived earlier; transient error → leave it alone
                e["missing_runs"] = 0
            e.setdefault("first_seen", now)
            entries[f.key] = e
            continue
        raw_rel, md_rel = entry_paths(site, f)
        (site.site_dir / raw_rel).parent.mkdir(parents=True, exist_ok=True)
        if f.key in diff["new"] or f.key in diff["changed"]:
            (site.site_dir / raw_rel).write_bytes(f.raw)
            (site.site_dir / md_rel).write_text(f.markdown, encoding="utf-8")
            if f.key in diff["changed"]:
                previous[f.key] = dict(prev)
        e = {
            "kind": f.kind, "source_url": f.source_url, "title": f.title,
            "raw_path": raw_rel, "md_path": md_rel,
            "sha256": sha256_text(f.markdown), "raw_sha256": sha256_bytes(f.raw),
            "words": word_count(f.markdown), "pages": f.pages or prev.get("pages", 0),
            "http_status": f.http_status, "note": f.note,
            "first_seen": prev.get("first_seen", now), "last_seen": now,
            "last_changed": now if (f.key in diff["new"] or f.key in diff["changed"]) else prev.get("last_changed", now),
            "status": "active", "missing_runs": 0,
            "ingest": prev.get("ingest") if f.key in diff["unchanged"] else None,
        }
        if f.key in diff["changed"]:
            # keep the last SUCCESSFUL version's record until it has been retired; a failed
            # attempt in between must not erase it.
            if (prev.get("ingest") or {}).get("ok"):
                e["previous_ingest"] = prev["ingest"]
            elif prev.get("previous_ingest"):
                e["previous_ingest"] = prev["previous_ingest"]
        elif prev.get("previous_ingest"):
            e["previous_ingest"] = prev["previous_ingest"]
        entries[f.key] = e
    for k in diff["missing"]:
        e = entries[k]
        e["missing_runs"] = int(e.get("missing_runs", 0)) + 1
        e["last_missing_run"] = run_id
    manifest["runs"] = int(manifest.get("runs", 0)) + 1
    manifest["last_run"] = run_id
    manifest["extractor_version"] = EXTRACTOR_VERSION
    return previous


def git(archive_root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(archive_root), "-c", "user.name=koi-website-sensor", "-c", "user.email=koi@localhost", *args],
        capture_output=True, text=True, check=check,
    )


def ensure_archive_repo(archive_root: Path) -> None:
    """A keep_history site needs a git repo; history-off sites are excluded from it."""
    if not (archive_root / ".git").exists():
        raise RuntimeError(f"{archive_root} is not a git repository — run `git init` there first "
                           f"(the archive for keep_history sites MUST be versioned)")
    gi = archive_root / ".gitignore"
    lines = gi.read_text().splitlines() if gi.exists() else []
    if "current/" not in lines:
        gi.write_text("\n".join(lines + ["current/"]) + "\n")


def git_commit(archive_root: Path, message: str) -> Optional[str]:
    ensure_archive_repo(archive_root)
    git(archive_root, "add", "-A")
    if not git(archive_root, "status", "--porcelain").stdout.strip():
        return None
    git(archive_root, "commit", "-q", "-m", message)
    return git(archive_root, "rev-parse", "--short", "HEAD").stdout.strip()


# ── DB helpers (asyncpg, small sync wrappers) ──────────────────────────────────

async def _db_measure(document_rid: str) -> Dict[str, Any]:
    import asyncpg
    conn = await asyncpg.connect(POSTGRES_URL)
    try:
        row = await conn.fetchrow(
            """SELECT
                 (SELECT COUNT(*) FROM koi_memory_chunks WHERE document_rid = $1) AS chunks,
                 (SELECT COUNT(*) FROM koi_memory_chunks WHERE document_rid = $1 AND embedding_3072 IS NULL) AS null_embeds,
                 (SELECT COUNT(*) FROM koi_memories WHERE rid = $1) AS memory_rows,
                 (SELECT COUNT(*) FROM document_field_membership WHERE document_rid = $1) AS fields""",
            document_rid,
        )
        return dict(row)
    finally:
        await conn.close()


OWNER_PREFIX = "website-sensor:"


async def _db_unretire(rid: str) -> None:
    """Content that reverts to an earlier version re-ingests onto a row that was superseded;
    clear the stamp so the live version is not hidden."""
    import asyncpg
    conn = await asyncpg.connect(POSTGRES_URL)
    try:
        await conn.execute("UPDATE koi_memories SET superseded_at = NULL, updated_at = NOW() WHERE rid = $1 AND superseded_at IS NOT NULL", rid)
    finally:
        await conn.close()


async def _db_retire(old_rid: str, new_rid: Optional[str], keep_history: bool = True) -> Dict[str, Any]:
    """keep_history: supersede old_rid (row kept + facts/claims/links; RAG chunks dropped).
    history off: delete the old document row + chunks. Facts/claims/entity links are left in
    both cases — their disposition is a processor-side policy (see stream-A design).
    Refuses to touch a row this sensor did not write (metadata.retrieval_method prefix):
    document_rid is a content hash, so the same bytes ingested by another path share it."""
    import asyncpg
    conn = await asyncpg.connect(POSTGRES_URL)
    try:
        async with conn.transaction():
            old = await conn.fetchrow(
                "SELECT id, version, metadata->>'retrieval_method' AS rm FROM koi_memories WHERE rid = $1", old_rid)
            if old is None:
                return {"old_found": False}
            if not (old["rm"] or "").startswith(OWNER_PREFIX):
                return {"old_found": True, "refused": f"not sensor-owned (retrieval_method={old['rm']!r})"}
            if not keep_history:
                deleted = await conn.execute("DELETE FROM koi_memory_chunks WHERE document_rid = $1", old_rid)
                await conn.execute("DELETE FROM koi_memories WHERE rid = $1", old_rid)
                return {"old_found": True, "chunks_deleted": int(deleted.split()[-1]), "row_deleted": True}
            await conn.execute(
                "UPDATE koi_memories SET superseded_at = COALESCE(superseded_at, NOW()), updated_at = NOW() WHERE rid = $1",
                old_rid,
            )
            if new_rid and new_rid != old_rid:
                await conn.execute(
                    """UPDATE koi_memories
                       SET previous_version_id = $2, version = COALESCE($3, 1) + 1, updated_at = NOW()
                       WHERE rid = $1""",
                    new_rid, old["id"], old["version"],
                )
            deleted = await conn.execute("DELETE FROM koi_memory_chunks WHERE document_rid = $1", old_rid)
            return {"old_found": True, "chunks_deleted": int(deleted.split()[-1])}
    finally:
        await conn.close()


def db_measure(rid: str) -> Dict[str, Any]:
    return asyncio.run(_db_measure(rid))


def db_retire(old_rid: str, new_rid: Optional[str], keep_history: bool = True) -> Dict[str, Any]:
    return asyncio.run(_db_retire(old_rid, new_rid, keep_history))


def db_unretire(rid: str) -> None:
    asyncio.run(_db_unretire(rid))


def db_ping() -> None:
    async def _p():
        import asyncpg
        conn = await asyncpg.connect(POSTGRES_URL)
        try:
            await conn.fetchval("SELECT 1")
        finally:
            await conn.close()
    asyncio.run(_p())


def rid_shared_by_other_entries(manifest: Dict[str, Any], key: str, rid: str) -> List[str]:
    """Other manifest entries whose live or previous version is this rid (identical content
    under two URLs). Retiring it for one key would silently retire it for the others."""
    out = []
    for k, e in manifest["entries"].items():
        if k == key or e.get("status") == "removed":
            continue
        if (e.get("ingest") or {}).get("document_rid") == rid or (e.get("previous_ingest") or {}).get("document_rid") == rid:
            out.append(k)
    return out


# ── Ingest ─────────────────────────────────────────────────────────────────────

RID_RE = re.compile(r"document_rid:\s+(document:[0-9a-f]{64})")


def ingest_one(site: SiteConfig, key: str, entry: Dict[str, Any], decision: Dict[str, Any]) -> Dict[str, Any]:
    md_path = site.site_dir / entry["md_path"]
    gate_dir = site.site_dir / ".gate"
    gate_dir.mkdir(exist_ok=True)
    slug = f"{site.site_id}--{Path(entry['md_path']).stem}"
    evidence = gate_dir / f"{Path(entry['md_path']).stem}.json"
    fields = [f for f in decision["fields"] if f and "," not in f]
    cmd = [
        sys.executable, str(REPO_ROOT / "scripts" / "ingest_document.py"),
        f"--source-path={md_path}", f"--tier={decision['tier']}",
        f"--group-id={decision['group_id']}", f"--source-url={entry['source_url']}",
        f"--name={decision.get('title') or entry.get('title') or slug}", f"--slug={slug}",
        f"--retrieval-method={OWNER_PREFIX}{site.site_id}",
        f"--gate-evidence-out={evidence}",
    ]
    if fields:
        cmd.append(f"--fields={','.join(fields)}")
    env = dict(os.environ)
    env["INGEST_SOURCE_ROOT"] = str(site.archive_root)        # path-safety allowlist → the archive
    env.setdefault("DOC_EPISODE_TIMEOUT", "900")
    started = time.monotonic()
    rec: Dict[str, Any] = {"tier": decision["tier"], "group_id": decision["group_id"],
                           "fields": decision["fields"], "started_at": utcnow(), "cmd": " ".join(cmd[1:])}
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
                            cwd=str(REPO_ROOT), start_new_session=True)   # own process group → kill extractor children too
    try:
        stdout, stderr = proc.communicate(timeout=site.ingest_timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        stdout, stderr = proc.communicate()
        m = RID_RE.search(stdout or "")
        rec.update({"ok": False, "error": f"timeout after {site.ingest_timeout}s", "document_rid": m.group(1) if m else None})
        return rec
    proc.stdout, proc.stderr = stdout, stderr
    rec["exit_code"] = proc.returncode
    rec["seconds"] = round(time.monotonic() - started, 1)
    m = RID_RE.search(proc.stdout)
    rec["document_rid"] = m.group(1) if m else None
    tail = (proc.stdout + "\n" + proc.stderr).strip().splitlines()[-12:]
    rec["log_tail"] = tail
    if proc.returncode != 0 or not rec["document_rid"]:
        rec.update({"ok": False, "error": f"ingest_document exit {proc.returncode}"})
        return rec
    # Gate (structural floors) — the same check the ingest-source skill runs by hand.
    if DEFAULT_GATE.exists() and evidence.exists():
        g = subprocess.run([sys.executable, str(DEFAULT_GATE), "--evidence", str(evidence), "--mode", "strict"],
                           capture_output=True, text=True)
        rec["gate"] = {"exit_code": g.returncode, "summary": (g.stdout.strip().splitlines() or [""])[-1]}
    else:
        rec["gate"] = {"exit_code": None, "summary": "gate script or evidence missing — NOT verified"}
    try:
        ev = json.loads(evidence.read_text())
        rec["evidence"] = {k: ev.get(k) for k in ("chunks_written", "facts_created", "entities_total",
                                                   "claims_created", "discourse_moves_created", "rag_null_embeds")}
    except Exception:
        rec["evidence"] = None
    # Measure the graph directly — the gate is a floor, not truth.
    try:
        rec["db"] = db_measure(rec["document_rid"])
    except Exception as e:
        rec["db"] = {"error": str(e)}
    db = rec["db"]
    measured_ok = isinstance(db.get("chunks"), int) and db["chunks"] > 0 and db.get("null_embeds") == 0
    gate_ok = rec["gate"]["exit_code"] == 0
    rec["ok"] = bool(measured_ok and gate_ok)
    if not rec["ok"]:
        rec["error"] = f"verification failed (gate_exit={rec['gate']['exit_code']}, db={db})"
    else:
        try:
            db_unretire(rec["document_rid"])            # content reverted to an earlier version?
        except Exception as e:
            rec["unretire_error"] = str(e)
    rec["finished_at"] = utcnow()
    return rec


def retire_previous(site: SiteConfig, manifest: Dict[str, Any], key: str) -> None:
    """Retire the persisted previous version of `key` once its current version is verified.
    Leaves previous_ingest in place on any failure so the next run retries."""
    entries = manifest["entries"]
    e = entries[key]
    old_rid = (e.get("previous_ingest") or {}).get("document_rid")
    new_rid = (e.get("ingest") or {}).get("document_rid")
    if not old_rid or not (e.get("ingest") or {}).get("ok") or old_rid == new_rid:
        if old_rid and old_rid == new_rid:
            e.pop("previous_ingest", None)
        return
    shared = rid_shared_by_other_entries(manifest, key, old_rid)
    if shared:
        e["retired_previous"] = {"document_rid": old_rid, "skipped": f"rid shared by {shared}"}
        e.pop("previous_ingest", None)
        return
    try:
        res = db_retire(old_rid, new_rid, site.keep_history)
        e["retired_previous"] = {"document_rid": old_rid, "at": utcnow(), **res}
        e.pop("previous_ingest", None)
    except Exception as ex:
        e["retired_previous"] = {"document_rid": old_rid, "error": str(ex), "at": utcnow()}


def run_ingests(site: SiteConfig, manifest: Dict[str, Any], keys: List[str], previous: Dict[str, Any],
                overrides: Dict[str, Any], max_ingest: Optional[int]) -> Dict[str, Any]:
    entries = manifest["entries"]
    todo: List[Tuple[str, Dict[str, Any]]] = []
    skipped: List[str] = []
    for k in keys:
        e = entries[k]
        if e.get("note") == "thin":
            skipped.append(f"{k} (thin: {e.get('words')} words)")
            continue
        d = route(site, k, overrides)
        if d["skip"]:
            skipped.append(f"{k} (routing skip)")
            continue
        prior = e.get("ingest") or {}
        if prior and not prior.get("ok") and int(prior.get("attempts", 1)) >= site.max_ingest_attempts \
                and prior.get("for_sha256") == e.get("sha256"):
            skipped.append(f"{k} (gave up after {prior.get('attempts')} failed attempts: {prior.get('error', '')[:80]})")
            logger.error("  GAVE UP  %s after %s attempts — %s", k[:70], prior.get("attempts"), prior.get("error", "")[:120])
            continue
        todo.append((k, d))
    if max_ingest is not None and len(todo) > max_ingest:
        logger.warning("max_ingest=%d: %d of %d documents deferred to the next run", max_ingest,
                       len(todo) - max_ingest, len(todo))
        todo = todo[:max_ingest]
    results: Dict[str, Dict[str, Any]] = {}
    logger.info("%s: ingesting %d documents (concurrency=%d)", site.site_id, len(todo), site.ingest_concurrency)
    with ThreadPoolExecutor(max_workers=max(1, site.ingest_concurrency)) as pool:
        futs = {pool.submit(ingest_one, site, k, entries[k], d): k for k, d in todo}
        for fut in as_completed(futs):
            k = futs[fut]
            try:
                rec = fut.result()
            except Exception as e:  # never let one document kill the run
                rec = {"ok": False, "error": f"exception: {e}"}
            prior = entries[k].get("ingest") or {}
            rec["attempts"] = (int(prior.get("attempts", 0)) if prior.get("for_sha256") == entries[k].get("sha256") else 0) + 1
            rec["for_sha256"] = entries[k].get("sha256")
            results[k] = rec
            entries[k]["ingest"] = rec
            status = "OK " if rec.get("ok") else "FAIL"
            logger.info("  [%s] %s → %s %s", status, k[:70], rec.get("document_rid", "-"),
                        "" if rec.get("ok") else rec.get("error", ""))
            # Retire the previous version only after the new one is verified. previous_ingest is
            # PERSISTED by write_snapshot, so a retry on a later run still retires it.
            retire_previous(site, manifest, k)
            save_manifest(site, manifest)                   # a kill mid-run loses nothing
    return {"attempted": len(todo), "ok": sum(1 for r in results.values() if r.get("ok")),
            "failed": [k for k, r in results.items() if not r.get("ok")], "skipped": skipped}


def retire_missing(site: SiteConfig, manifest: Dict[str, Any], discovery_healthy: bool) -> List[str]:
    """Mark entries missing for ≥ retire_after_missing_runs consecutive runs as removed:
    delete the working-tree files (history keeps them), supersede the memory row, drop chunks."""
    if site.on_removed != "retire" or not discovery_healthy:
        return []
    removed: List[str] = []
    errors: List[str] = []
    head = git(site.archive_root, "rev-parse", "--short", "HEAD").stdout.strip() if site.keep_history else None
    for k, e in manifest["entries"].items():
        if e.get("status") == "removed" or int(e.get("missing_runs", 0)) < site.retire_after_missing_runs:
            continue
        rids = [r for r in {(e.get("ingest") or {}).get("document_rid"),
                            (e.get("previous_ingest") or {}).get("document_rid")} if r]
        retired: List[Dict[str, Any]] = []
        failed = False
        for rid in rids:
            shared = rid_shared_by_other_entries(manifest, k, rid)
            if shared:
                retired.append({"document_rid": rid, "skipped": f"rid shared by {shared}"})
                continue
            try:
                retired.append({"document_rid": rid, **db_retire(rid, None, site.keep_history)})
            except Exception as ex:
                retired.append({"document_rid": rid, "error": str(ex)})
                failed = True
        e["retired"] = retired
        if failed:                                        # stay active; retry next run; files kept
            e["retire_error_at"] = utcnow()
            errors.append(k)
            continue
        for rel in (e.get("raw_path"), e.get("md_path")):
            if rel:
                (site.site_dir / rel).unlink(missing_ok=True)
        e.update({"status": "removed", "removed_at": utcnow(), "last_commit_with_file": head})
        e.pop("previous_ingest", None)
        removed.append(k)
    if errors:
        logger.error("%s: DB retirement failed for %d entr%s — left active for retry: %s", site.site_id,
                     len(errors), "y" if len(errors) == 1 else "ies", errors)
    manifest["retire_errors"] = errors
    return removed


# ── Orchestration ──────────────────────────────────────────────────────────────

def preflight(site: SiteConfig, mode: str) -> None:
    """Discover misconfiguration BEFORE fetching anything or touching the DB."""
    problems = []
    if not shutil.which("pdftotext"):
        problems.append("pdftotext not on PATH (brew install poppler)")
    if mode != "dry-run":
        if site.keep_history and not (site.archive_root / ".git").exists():
            problems.append(f"{site.archive_root} is not a git repo (keep_history=true requires one)")
        if mode == "ingest":
            if not DEFAULT_GATE.exists():
                problems.append(f"document-ingest gate not found at {DEFAULT_GATE} (set DOC_INGEST_GATE)")
            if not (REPO_ROOT / "scripts" / "ingest_document.py").exists():
                problems.append("scripts/ingest_document.py missing")
            try:
                db_ping()
            except Exception as e:
                problems.append(f"database unreachable ({POSTGRES_URL.split('@')[-1]}): {e}")
    if problems:
        raise RuntimeError(f"{site.site_id}: preflight failed: " + "; ".join(problems))


class RunLock:
    """One sensor run per archive root at a time (launchd + a manual run must not race)."""
    def __init__(self, archive_root: Path):
        archive_root.mkdir(parents=True, exist_ok=True)
        self.path = archive_root / ".website-sensor.lock"
        self.fh = None

    def __enter__(self):
        self.fh = open(self.path, "w")
        try:
            fcntl.flock(self.fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError(f"another website-sensor run holds {self.path}")
        self.fh.write(f"{os.getpid()} {utcnow()}\n")
        self.fh.flush()
        return self

    def __exit__(self, *exc):
        fcntl.flock(self.fh, fcntl.LOCK_UN)
        self.fh.close()


def run_site(site: SiteConfig, mode: str, max_docs: Optional[int], max_ingest: Optional[int],
             tmp_dir: Path) -> Dict[str, Any]:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    summary: Dict[str, Any] = {"site_id": site.site_id, "run_id": run_id, "mode": mode, "started_at": utcnow()}
    preflight(site, mode)
    fetcher = Fetcher(max_bytes=site.max_download_mb * 1024 * 1024)
    try:
        fetched, report = snapshot_site(site, fetcher, tmp_dir, max_docs=max_docs)
    finally:
        fetcher.close()
    manifest = load_manifest(site)
    prior_ev = manifest.get("extractor_version")
    diff = classify(site, manifest, fetched)
    if prior_ev and prior_ev != EXTRACTOR_VERSION and diff["changed"]:
        logger.warning("%s: EXTRACTOR CHANGED %s → %s — %d of the %d 'changed' documents may be "
                       "re-extraction churn, not site edits; each re-ingests and retires its previous version",
                       site.site_id, prior_ev, EXTRACTOR_VERSION, len(diff["changed"]),
                       len(diff["changed"]) + len(diff["unchanged"]))
        summary["extractor_changed"] = {"from": prior_ev, "to": EXTRACTOR_VERSION, "changed": len(diff["changed"])}
    summary["extractor_version"] = EXTRACTOR_VERSION
    summary["discovery"] = report
    summary["counts"] = {k: len(v) for k, v in diff.items()}
    logger.info("%s: new=%d changed=%d unchanged=%d unavailable=%d missing=%d", site.site_id,
                *(len(diff[k]) for k in ("new", "changed", "unchanged", "unavailable", "missing")))
    for k in diff["new"]:
        logger.info("  NEW      %s", k)
    for k in diff["changed"]:
        logger.info("  CHANGED  %s", k)
    for k in diff["missing"]:
        logger.info("  MISSING  %s (run %d of %d before retire)", k,
                    int(manifest["entries"][k].get("missing_runs", 0)) + 1, site.retire_after_missing_runs)
    for k in diff["unavailable"]:
        f = next(x for x in fetched if x.key == k)
        logger.info("  UNAVAIL  %s [%s]", k, f.note)

    if mode == "dry-run":
        overrides = load_overrides(site)
        summary["plan"] = [{"key": k, **route(site, k, overrides)} for k in diff["new"] + diff["changed"]]
        summary["finished_at"] = utcnow()
        logger.info("%s: DRY RUN — nothing written", site.site_id)
        return summary

    previous = write_snapshot(site, manifest, fetched, diff, run_id)
    save_manifest(site, manifest)
    if site.keep_history:
        commit = git_commit(site.archive_root, f"{site.site_id} snapshot {run_id}: +{len(diff['new'])} "
                                               f"~{len(diff['changed'])} -{len(diff['missing'])} missing")
        logger.info("%s: archive committed %s", site.site_id, commit or "(no changes)")
    else:
        commit = None
        logger.info("%s: keep_history=false — snapshot overwritten in place, no git", site.site_id)
    summary["commit"] = commit

    if mode == "archive-only":
        summary["finished_at"] = utcnow()
        _append_run(site, summary)
        if site.keep_history:
            git_commit(site.archive_root, f"{site.site_id} run log {run_id}")
        return summary

    overrides = load_overrides(site)
    # Due = fetched OK this run and not yet verified-ingested (new, changed, or an earlier
    # failure). Entries that were unavailable or missing this run are never ingested from
    # the archive: the site did not serve them, so nothing new is asserted about them.
    due = [k for k in diff["new"] + diff["changed"]]
    for k in diff["unchanged"]:
        if not (manifest["entries"][k].get("ingest") or {}).get("ok"):
            due.append(k)
    summary["ingest"] = run_ingests(site, manifest, due, previous, overrides, max_ingest)
    # Retry any previous-version retirement that failed on an earlier run.
    for k, e in manifest["entries"].items():
        if e.get("previous_ingest") and (e.get("ingest") or {}).get("ok") and k not in due:
            retire_previous(site, manifest, k)
    discovery_healthy = bool(report["sitemap_urls"] or report["crawl_urls"]) and not any(
        e.startswith("sitemap") for e in report["errors"])
    summary["removed"] = retire_missing(site, manifest, discovery_healthy)
    summary["retire_errors"] = manifest.get("retire_errors", [])
    save_manifest(site, manifest)
    summary["finished_at"] = utcnow()
    _append_run(site, summary)
    if site.keep_history:
        summary["commit_after_ingest"] = git_commit(site.archive_root, f"{site.site_id} ingest {run_id}: "
                                                    f"{summary['ingest']['ok']}/{summary['ingest']['attempted']} ok, "
                                                    f"{len(summary['removed'])} removed")
    return summary


def _append_run(site: SiteConfig, summary: Dict[str, Any]) -> None:
    with (site.site_dir / "runs.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(summary, ensure_ascii=False, default=str) + "\n")


def print_status(sites: List[SiteConfig]) -> None:
    for site in sites:
        m = load_manifest(site)
        entries = m["entries"]
        by = {}
        for e in entries.values():
            st = e.get("status", "?")
            ing = (e.get("ingest") or {})
            k = f"{st}/{'ingested' if ing.get('ok') else ('failed' if ing else 'not-ingested')}"
            by[k] = by.get(k, 0) + 1
        head = git(site.archive_root, "rev-parse", "--short", "HEAD", check=False).stdout.strip() if (
            site.archive_root / ".git").exists() else "(no repo)"
        print(f"{site.site_id}  {site.root_url}  runs={m.get('runs', 0)}  last={m.get('last_run', '-')}  "
              f"archive@{head}  entries={len(entries)}")
        for k, v in sorted(by.items()):
            print(f"   {k}: {v}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--site", help="only this site_id")
    ap.add_argument("--dry-run", action="store_true", help="fetch + plan only; write nothing (wins over everything)")
    ap.add_argument("--archive-only", action="store_true", help="snapshot + git commit; no ingest / retire")
    ap.add_argument("--max-docs", type=int, help="cap linked documents fetched per site (smoke test)")
    ap.add_argument("--max-ingest", type=int, help="cap documents ingested per site this run")
    ap.add_argument("--status", action="store_true", help="print manifest summaries and exit")
    args = ap.parse_args()

    sites = load_config(Path(args.config).expanduser())
    if args.site:
        sites = [s for s in sites if s.site_id == args.site]
        if not sites:
            print(f"no site {args.site!r} in config", file=sys.stderr)
            return 2
    if args.status:
        print_status(sites)
        return 0
    mode = "dry-run" if args.dry_run else ("archive-only" if args.archive_only else "ingest")
    rc = 0
    with tempfile.TemporaryDirectory(prefix="koi-website-sensor-") as td:
        for site in sites:
            try:
                lock = RunLock(site.archive_root) if mode != "dry-run" else None
                if lock:
                    lock.__enter__()
                try:
                    s = run_site(site, mode, args.max_docs, args.max_ingest, Path(td))
                finally:
                    if lock:
                        lock.__exit__(None, None, None)
                print(json.dumps(s, indent=2, default=str))
                if s.get("ingest", {}).get("failed") or s.get("retire_errors") or s.get("discovery", {}).get("errors"):
                    rc = 1
            except Exception as e:
                logger.error("%s: run failed: %s", site.site_id, e, exc_info=True)
                rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
