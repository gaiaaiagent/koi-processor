import json
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import ingest_substack_corpus as corpus  # noqa: E402
import substack_sensor as sensor  # noqa: E402


@pytest.mark.parametrize(
    ("audience", "is_private", "access_source"),
    [
        ("everyone", False, "substack-public"),
        ("only_paid", True, "substack-subscriber-session"),
        ("founding", True, "substack-subscriber-session"),
    ],
)
def test_api_audience_controls_access(audience, is_private, access_source):
    policy = sensor.access_policy_for_audience(audience)

    assert policy == {
        "audience": audience,
        "is_private": is_private,
        "access_source": access_source,
        "source_provenance": "substack-api-public" if not is_private else "substack-api-subscriber-session",
    }


def test_api_detail_audience_takes_precedence_with_archive_fallback():
    assert sensor.resolved_audience(
        {"audience": "everyone"}, {"audience": "only_paid"}
    ) == "only_paid"
    assert sensor.resolved_audience(
        {"audience": "founding"}, {}
    ) == "founding"


class _AsyncContext:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _ApiConn:
    def __init__(self):
        self.calls = []

    def transaction(self):
        return _AsyncContext(self)

    async def fetchrow(self, sql, *args):
        return {"id": "existing"}

    async def execute(self, sql, *args):
        self.calls.append((sql, args))


class _ApiPool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return _AsyncContext(self.conn)


class _Embedder:
    async def embed(self, text):
        return [0.1, 0.2]


class _Chunker:
    def chunk_text(self, text):
        return [{"text": text}]


class _Http:
    def __init__(self, status_code=200, error=None):
        self.status_code = status_code
        self.error = error
        self.calls = []

    async def post(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.error:
            raise self.error
        return type("Response", (), {"status_code": self.status_code})()


@pytest.mark.asyncio
async def test_api_upsert_writes_and_updates_private_access_fields():
    conn = _ApiConn()
    pub = {
        "feed_slug": "author",
        "base": "https://author.substack.com",
        "tags": ["substack"],
        "author": "Author",
        "domain": "commons",
        "author_entity": {"name": "Author", "type": "Person"},
    }

    await sensor.upsert_post(
        _ApiPool(conn), _Embedder(), _Chunker(), _Http(), pub, "paid-post",
        {
            "title": "Paid post",
            "subtitle": "",
            "post_date": "2026-09-13T10:00:00Z",
            "audience": "only_paid",
        },
        "full subscriber text", False,
    )

    memory_sql, memory_args = conn.calls[0]
    assert "is_private = EXCLUDED.is_private" in memory_sql
    assert "access_source = EXCLUDED.access_source" in memory_sql
    assert memory_args[5:7] == (True, "substack-subscriber-session")
    memory_metadata = json.loads(memory_args[4])
    assert memory_metadata["audience"] == "only_paid"
    assert memory_metadata["source_provenance"] == "substack-api-subscriber-session"

    chunk_sql, chunk_args = conn.calls[2]
    assert "INSERT INTO koi_memory_chunks" in chunk_sql
    chunk_metadata = json.loads(chunk_args[6])
    assert chunk_metadata["audience"] == "only_paid"
    assert chunk_metadata["is_private"] is True
    assert chunk_metadata["access_source"] == "substack-subscriber-session"


def test_gmail_corpus_carries_private_provenance_and_full_parent_text(tmp_path):
    full_text = "subscriber article " * 200
    path = tmp_path / "author_from_gmail.json"
    path.write_text(json.dumps({
        "note": "harvested from Gmail (paid full-content post emails, via IMAP)",
        "posts": [{
            "url": "https://author.substack.com/p/private-post",
            "title": "Private post",
            "date": "2026-09-13T10:00:00+00:00",
            "full_content": full_text,
        }],
    }))

    post = corpus.parse_corpus(str(path))[0]
    content, metadata, access = corpus.build_parent_payload(
        post=post,
        slug="private-post",
        feed_slug="author",
        author="Author",
        domain="commons",
        tags=["substack"],
    )

    assert content["text"] == full_text
    assert access == {
        "audience": "subscriber_email",
        "is_private": True,
        "access_source": "substack-gmail-subscriber",
        "source_provenance": "gmail-subscriber-email",
    }
    assert metadata["audience"] == "subscriber_email"
    assert metadata["is_private"] is True
    assert metadata["access_source"] == "substack-gmail-subscriber"
    assert metadata["source_provenance"] == "gmail-subscriber-email"

    chunk_metadata = corpus.build_chunk_metadata(
        post=post,
        slug="private-post",
        feed_slug="author",
        author="Author",
        domain="commons",
        tags=["substack"],
    )
    for key, value in access.items():
        assert chunk_metadata[key] == value


class _FakeConn:
    def __init__(self, inserted_rid):
        self.inserted_rid = inserted_rid
        self.calls = []

    async def fetchval(self, sql, *args):
        self.calls.append(("fetchval", sql, args))
        return self.inserted_rid

    async def execute(self, sql, *args):
        self.calls.append(("execute", sql, args))


@pytest.mark.asyncio
async def test_corpus_conflict_does_not_touch_existing_canonical_chunks():
    conn = _FakeConn(inserted_rid=None)

    inserted, chunk_count = await corpus.write_corpus_post(
        conn=conn,
        document_rid="substack-corpus:author:existing",
        parent_content={"text": "complete text"},
        parent_metadata={"canonical_slug": "existing"},
        published_at=None,
        access={
            "audience": "subscriber_email",
            "is_private": True,
            "access_source": "substack-gmail-subscriber",
            "source_provenance": "gmail-subscriber-email",
        },
        chunks=[{"index": 0, "text": "complete text"}],
        embeddings=[[0.1, 0.2]],
        chunk_metadata={"canonical_slug": "existing"},
    )

    assert inserted is False
    assert chunk_count == 0
    assert [call[0] for call in conn.calls] == ["fetchval"]
    insert_sql = conn.calls[0][1]
    assert "ON CONFLICT (rid) DO NOTHING" in insert_sql
    assert "RETURNING rid" in insert_sql


@pytest.mark.asyncio
async def test_corpus_insert_writes_private_access_columns():
    rid = "substack-corpus:author:new-paid"
    conn = _FakeConn(inserted_rid=rid)
    access = {
        "audience": "subscriber_email",
        "is_private": True,
        "access_source": "substack-gmail-subscriber",
        "source_provenance": "gmail-subscriber-email",
    }

    inserted, chunk_count = await corpus.write_corpus_post(
        conn=conn,
        document_rid=rid,
        parent_content={"text": "complete text"},
        parent_metadata={"canonical_slug": "new-paid", **access},
        published_at=None,
        access=access,
        chunks=[{"index": 0, "text": "complete text"}],
        embeddings=[[0.1, 0.2]],
        chunk_metadata={"canonical_slug": "new-paid", **access},
    )

    assert inserted is True
    assert chunk_count == 1
    memory_args = conn.calls[0][2]
    assert memory_args[5:7] == (True, "substack-gmail-subscriber")
    chunk_metadata = json.loads(conn.calls[1][2][6])
    assert chunk_metadata["audience"] == "subscriber_email"
    assert chunk_metadata["source_provenance"] == "gmail-subscriber-email"


@pytest.mark.asyncio
async def test_new_corpus_post_links_author_through_local_ingest(monkeypatch):
    monkeypatch.setattr(corpus, "KOI_BASE_URL", "http://localhost:8351")
    http = _Http()

    linked = await corpus.link_author_for_inserted_post(
        inserted=True,
        http=http,
        document_rid="substack-corpus:michaelgarfield:newsletter-only",
        author="Michael Garfield",
        title="Newsletter only",
        content="complete newsletter text",
    )

    assert linked is True
    assert len(http.calls) == 1
    args, kwargs = http.calls[0]
    assert args == ("http://localhost:8351/ingest",)
    assert kwargs["json"] == {
        "document_rid": "substack-corpus:michaelgarfield:newsletter-only",
        "content": "complete newsletter text",
        "entities": [{
            "name": "Michael Garfield",
            "type": "Person",
            "confidence": 0.99,
            "context": "Author of Substack post: Newsletter only",
        }],
        "source": corpus.SOURCE_SENSOR,
    }
    assert kwargs["timeout"] == 30.0


@pytest.mark.asyncio
async def test_corpus_conflict_does_not_call_author_link_service():
    http = _Http()

    linked = await corpus.link_author_for_inserted_post(
        inserted=False,
        http=http,
        document_rid="substack-corpus:author:existing",
        author="Author",
        title="Existing",
        content="text",
    )

    assert linked is False
    assert http.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [RuntimeError("service down"), None])
async def test_author_link_failure_does_not_fail_corpus_ingest(failure):
    http = _Http(status_code=503, error=failure)

    linked = await corpus.link_author_for_inserted_post(
        inserted=True,
        http=http,
        document_rid="substack-corpus:author:new",
        author="Author",
        title="New",
        content="text",
    )

    assert linked is False
