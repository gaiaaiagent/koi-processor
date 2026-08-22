"""Policy for landing federated DOCUMENTS into local RAG storage.

This module holds the three decisions that gate the `document` federation
domain, deliberately separated from the handler itself (`domain_event_handlers.
_apply_document`) so each is testable without a database:

1. **Is the feature on at all?** `KOI_FEDERATE_DOCUMENTS`, default **off**.
   `regen-prod` rsyncs to the NUC, whose operator has not opted in to receiving
   a new dispatcher + document sink. Default-off keeps that node byte-identical
   in behaviour until it sets the flag itself.
2. **Is this RID allowed to land?** Server-side `rid_types` edge scoping is
   verified ineffective for `regen.newsletter:` RIDs — `extract_rid_type()`
   returns None for them and the poll filter only skips when a type *is*
   extracted. Containment therefore has to be enforced here, on the receiving
   side, or an over-broad edge silently lands unrelated coordinator content.
3. **Who wrote it?** Coordinator newsletter bundles carry a `document.author`
   key whose value is **null in all 448 bundles measured** (2026-08-21). The
   key existing is not the value existing. Author is therefore DERIVED from
   `newsletter_slug` via an explicit table, and an unmapped slug is a loud
   failure — a null author is exactly what lets "what does Nate say" answer
   confidently with Nate Hagens (476 of his documents are already indexed).

None of these fail open. An empty allowlist allows nothing; an unmapped slug
raises; an unset flag disables the path entirely.
"""

import os
from typing import Any, Dict, Mapping, Optional

# The domain name used on the wire and in the handler registry.
DOCUMENT_DOMAIN = "document"

# Default containment: only Nate B. Jones' newsletter may land. Overridable via
# KOI_FEDERATE_DOCUMENTS_RID_ALLOW (comma-separated substrings) when the sink is
# generalized to other publications — at which point NEWSLETTER_SLUG_AUTHORS
# must gain the matching entry, or ingestion of the new slug fails loudly.
DEFAULT_RID_ALLOWLIST = ("newsletter_nate-jones-substack_",)

# newsletter_slug → author. See module docstring: derived, never copied.
NEWSLETTER_SLUG_AUTHORS: Dict[str, str] = {
    "nate-jones-substack": "Nate B. Jones",
}

# newsletter_slug → the host a canonical post URL must live on. Any URL on some
# other host is discarded rather than stored (see `canonical_url`).
NEWSLETTER_SLUG_HOSTS: Dict[str, str] = {
    "nate-jones-substack": "natesnewsletter.substack.com",
}

# Provenance stamp distinguishing federated documents from locally-ingested
# ones in koi_memories.source_sensor.
FEDERATION_SOURCE_SENSOR = "koi-net-federation"


class UnmappedNewsletterSlug(Exception):
    """Raised when a document's slug has no author mapping.

    Deliberately fatal for the event rather than defaulted: writing a null
    author is silent coercion of an unknown into the benign value, and the
    resulting document is indistinguishable from another author's work at
    query time.
    """


def document_federation_enabled() -> bool:
    """Whether federated documents may land locally. Default **off**.

    Re-read on every call (matching `_knowledge_federation_enabled`) so the
    operator can flip it without restarting the service.
    """
    return os.getenv("KOI_FEDERATE_DOCUMENTS", "false").strip().lower() in {
        "1", "true", "yes", "on",
    }


def rid_allowlist() -> tuple[str, ...]:
    """Substrings a document RID must contain to be allowed to land.

    An unset variable yields the default. A variable set to an empty/whitespace
    string yields an EMPTY tuple, which allows nothing — the fail-closed
    reading. "Allow everything" has no representation on purpose.
    """
    raw = os.getenv("KOI_FEDERATE_DOCUMENTS_RID_ALLOW")
    if raw is None:
        return DEFAULT_RID_ALLOWLIST
    return tuple(p.strip() for p in raw.split(",") if p.strip())


def rid_allowed(rid: str) -> bool:
    """True when `rid` matches the containment allowlist."""
    if not rid:
        return False
    return any(pattern in rid for pattern in rid_allowlist())


def is_document_payload(contents: Any) -> bool:
    """True when `contents` has the shape of a federated document bundle.

    Coordinator bundles are `{document: {...}, metadata: {...}, processing:
    {...}}` and carry NO `_koi_domain` marker — that marker is written only by
    this node's own domain emitters. Shape is therefore the only signal
    available on the receive side.
    """
    if not isinstance(contents, Mapping):
        return False
    doc = contents.get("document")
    return isinstance(doc, Mapping) and bool(doc.get("content"))


def should_dispatch_as_document(rid: str, contents: Any) -> bool:
    """Whether the poller should route this event to the document handler.

    Matches either the bundle shape (a NEW/UPDATE carrying a body) or a
    contents-less event on an already-allowed RID (a FORGET). Both are then
    re-checked against the allowlist inside the handler, so this predicate
    being generous cannot itself land anything.
    """
    if not document_federation_enabled():
        return False
    if is_document_payload(contents):
        return True
    return rid_allowed(rid)


def newsletter_slug(contents: Mapping[str, Any]) -> Optional[str]:
    """Extract the publication slug from a bundle.

    Prefers `metadata.newsletter_slug` (populated 448/448 in the measured
    corpus) and falls back to the suffix of `document.source`
    (`newsletters:nate-jones-substack`).
    """
    meta = contents.get("metadata")
    if isinstance(meta, Mapping):
        slug = meta.get("newsletter_slug")
        if isinstance(slug, str) and slug.strip():
            return slug.strip()
    doc = contents.get("document")
    if isinstance(doc, Mapping):
        source = doc.get("source")
        if isinstance(source, str) and ":" in source:
            tail = source.split(":", 1)[1].strip()
            if tail:
                return tail
    return None


def resolve_author(slug: Optional[str]) -> str:
    """Map a publication slug to its author, or fail loudly.

    Never returns None and never consults `document.author` — that field is
    null across the entire measured corpus.
    """
    if not slug:
        raise UnmappedNewsletterSlug(
            "document has no newsletter_slug and no derivable document.source "
            "suffix; refusing to land it with a null author"
        )
    author = NEWSLETTER_SLUG_AUTHORS.get(slug)
    if not author:
        raise UnmappedNewsletterSlug(
            f"newsletter_slug {slug!r} has no entry in NEWSLETTER_SLUG_AUTHORS "
            f"(known: {sorted(NEWSLETTER_SLUG_AUTHORS)}). Add one before "
            f"widening KOI_FEDERATE_DOCUMENTS_RID_ALLOW — a null author makes "
            f"this document indistinguishable from another author's."
        )
    return author


def canonical_url(slug: Optional[str], contents: Mapping[str, Any]) -> Optional[str]:
    """The post's canonical URL, or None when the bundle does not carry one.

    **Measured 2026-08-21 across all 448 bundles — this corrects a plan
    assumption.** The plan recorded `document.url` as "448/448 canonical
    natesnewsletter.substack.com/p/...". It is **336/448**. The other 112 (25%)
    carry a tracking-pixel URL — `https://eotrx.substackcdn.com/o/<id>/p.gif
    ?token=<jwt>` — in BOTH `document.url` and `metadata.url`, so there is no
    fallback field inside the bundle. 224 such values base64-decode to a token
    containing the subscriber's own email address.

    Two consequences, both handled here by returning None:

    - A tracking pixel is not provenance. It does not resolve to the post and
      would be presented to a reader as the source link.
    - It is PII. `metadata.source_url` is copied onto every chunk so that search
      results can cite the source, which is precisely where a subscriber's email
      address should not appear.

    This deliberately does NOT reconstruct the URL by scanning the body: only 22
    of the 112 contain a canonical `/p/` link at all, and a body scan can match a
    link to a *different* post that the author referenced — producing a
    confidently wrong provenance link, which is worse than an absent one. The
    remaining 90 have no canonical URL anywhere in the snapshot; recovering those
    needs the archive listing, not a heuristic.
    """
    host = NEWSLETTER_SLUG_HOSTS.get(slug or "")
    doc = contents.get("document")
    meta = contents.get("metadata")
    candidates = []
    if isinstance(doc, Mapping):
        candidates.append(doc.get("url"))
    if isinstance(meta, Mapping):
        candidates.append(meta.get("url"))
    for url in candidates:
        if not isinstance(url, str) or not url.strip():
            continue
        url = url.strip()
        if not host:
            # Unknown publication: no host to validate against. Refuse rather
            # than trust — the allowlist should have stopped this already.
            return None
        if url.startswith(f"https://{host}/"):
            return url
    return None
