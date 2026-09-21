import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "research_author_sensor.py"


def load_sensor():
    spec = importlib.util.spec_from_file_location("research_author_sensor", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def make_author(tmp_path):
    sensor = load_sensor()
    return sensor.AuthorConfig(
        author_id="ghrist-robert",
        canonical_name="Robert Ghrist",
        aliases=["R. Ghrist"],
        corpus_root=tmp_path / "Papers",
        author_dir=tmp_path / "Papers/authors/ghrist-robert",
        official_preprints="https://www2.math.upenn.edu/~ghrist/preprints.html",
        arxiv_queries=['au:"Robert Ghrist"'],
        project_tags=["sheaf-explorer", "spore"],
        corpus_tags=["sheaf-theory", "applied-topology"],
        direct_patterns=["sheaf", "cohomology", "discourse"],
        project_patterns=["network", "robot", "persistence"],
    )


def test_parse_official_preprints(tmp_path):
    sensor = load_sensor()
    author = make_author(tmp_path)
    html = """
    <html><body><ul>
      <li>[2026] <a href="preprints/localglobal.pdf">Neural Networks as Local-to-Global Computations</a></li>
      <li>[2022] Network Sheaf Models for Social Information Systems</li>
    </ul></body></html>
    """

    records = sensor.parse_official_preprints(html, author)

    assert len(records) == 2
    assert records[0].year == 2026
    assert records[0].source_url == "https://www2.math.upenn.edu/~ghrist/preprints/localglobal.pdf"
    assert records[1].title == "Network Sheaf Models for Social Information Systems"


def test_parse_arxiv_xml(tmp_path):
    sensor = load_sensor()
    author = make_author(tmp_path)
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <id>http://arxiv.org/abs/2603.14831v3</id>
        <updated>2026-03-20T00:00:00Z</updated>
        <published>2026-03-18T00:00:00Z</published>
        <title>Neural Networks as Local-to-Global Computations</title>
        <summary>We study sheaf-style local-to-global computation.</summary>
        <author><name>Victor Bosca</name></author>
        <author><name>Robert Ghrist</name></author>
      </entry>
    </feed>
    """

    records = sensor.parse_arxiv_xml(xml, author)

    assert len(records) == 1
    record = records[0]
    assert record.year == 2026
    assert record.arxiv_id == "2603.14831"
    assert record.arxiv_version == "v3"
    assert record.pdf_url == "https://arxiv.org/pdf/2603.14831"
    assert record.authors == ["Victor Bosca", "Robert Ghrist"]


def test_merge_scores_and_keeps_official_source(tmp_path):
    sensor = load_sensor()
    author = make_author(tmp_path)
    official = sensor.PaperRecord(
        author_id=author.author_id,
        canonical_author=author.canonical_name,
        title="Neural Networks as Local-to-Global Computations",
        year=2026,
        source_url="https://www2.math.upenn.edu/~ghrist/preprints/localglobal.pdf",
        source_kinds=["official_preprints"],
    )
    arxiv = sensor.PaperRecord(
        author_id=author.author_id,
        canonical_author=author.canonical_name,
        title="Neural Networks as Local-to-Global Computations",
        year=2026,
        abstract="This paper uses sheaf cohomology language for neural network computation.",
        source_url="https://arxiv.org/abs/2603.14831v3",
        pdf_url="https://arxiv.org/pdf/2603.14831",
        arxiv_id="2603.14831",
        source_kinds=["arxiv"],
    )

    merged = sensor.merge_records([official, arxiv])
    sensor.apply_record_ids(merged)
    score, matches, decision = sensor.score_record(merged[0], author)

    assert len(merged) == 1
    assert merged[0].official_pdf_or_page == "https://www2.math.upenn.edu/~ghrist/preprints/localglobal.pdf"
    assert merged[0].source_url == "https://arxiv.org/abs/2603.14831v3"
    assert merged[0].paper_id == "ghrist-robert/2026-neural-networks-as-local-to-global-computations"
    assert score >= 6
    assert "sheaf" in matches
    assert decision == "download_now"


def test_existing_index_uses_manifest_and_metadata_titles(tmp_path):
    sensor = load_sensor()
    author = make_author(tmp_path)
    author.author_dir.mkdir(parents=True)
    paper_dir = author.author_dir / "2026-existing-paper"
    paper_dir.mkdir()
    (paper_dir / "metadata.yaml").write_text('title: "[2026] Existing Paper Title"\n')
    manifest = author.corpus_root / "manifest.jsonl"
    manifest.write_text(
        '{"paper_id":"ghrist-robert/2025-manifest-paper","title":"Manifest Paper"}\n'
    )
    records = [
        sensor.PaperRecord(
            author_id=author.author_id,
            canonical_author=author.canonical_name,
            title="Manifest Paper",
            year=2025,
        ),
        sensor.PaperRecord(
            author_id=author.author_id,
            canonical_author=author.canonical_name,
            title="Existing Paper Title",
            year=2026,
        ),
    ]
    sensor.apply_record_ids(records)

    ids, titles = sensor.load_existing_index(author)
    sensor.mark_existing(records, ids, titles)

    assert records[0].existing is True
    assert records[0].existing_reason == "paper_id"
    assert records[1].existing is True
    assert records[1].existing_reason == "title"
