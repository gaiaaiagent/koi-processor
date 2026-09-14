"""Semantic-quality assessment for a document extraction — separate from completion.

Issue #64. The document-ingest completion gate establishes that every stage ran and
met a structural floor. Its own catalog says so: "green = every stage ran and met its
structural floor, NOT the extracted knowledge is good. A gibberish document that
chunks and yields >= 1 fact passes strict."

That is not a hypothetical. The original Buehler (2024) run passed the strict gate
with 25 facts and 25 discourse moves; a curated re-run of the *same document* yielded
115 entity endpoints, 213 facts and 52 discourse moves. Both were "complete". An
operator reading a green gate could not tell them apart.

THE DESIGN CONSTRAINT THAT SHAPES EVERYTHING HERE
-------------------------------------------------
#64 requirement 5: "avoid inert count-only thresholds that reward hallucinated
volume." A floor like `facts >= 40` is satisfied just as well by forty invented facts
as by forty real ones — and an extractor that pads is *easier* to satisfy than one
that is careful. So no dimension below is a raw count. Every one is a RATIO whose
denominator is a property of the source document, and the two load-bearing ones
(coverage, citation support) get WORSE when output is padded:

  - coverage divides by the document's own window count, so extracting the intro
    forty times over does not improve it;
  - citation support divides by the number of facts, so an unsupported fact lowers
    the score it was meant to raise.

Everything is computed deterministically from the payload and the source text. No
model is called: a model grading another model's output would make the measurement
depend on the thing being measured, and would cost a provider call per document.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

logger = logging.getLogger(__name__)

QUALITY_CONTRACT_VERSION = "extraction-quality-v1"

STATUS_PASS = "pass"
STATUS_REVIEW = "review"
STATUS_FAIL = "fail"
STATUS_NOT_EVALUATED = "not_evaluated"

# Tokens too common to be evidence that a fact is grounded in its cited span.
_STOPWORDS = frozenset("""
a an and are as at be by for from has have in is it its of on or that the their this
to was were will with which who whom whose then than these those there here we our
""".split())

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _content_tokens(text: Optional[str]) -> set:
    if not text:
        return set()
    return {t for t in _TOKEN_RE.findall(text.lower())
            if t not in _STOPWORDS and len(t) > 2}


@dataclass
class Dimension:
    """One quality dimension: a verdict, the number behind it, and the evidence."""
    name: str
    status: str
    value: Optional[float]
    threshold: Optional[float]
    detail: str
    evidence: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "name": self.name, "status": self.status, "value": self.value,
            "threshold": self.threshold, "detail": self.detail,
            "evidence": self.evidence,
        }


@dataclass
class QualityReport:
    status: str
    dimensions: list
    tier: str
    contract_version: str = QUALITY_CONTRACT_VERSION
    waiver: Optional[dict] = None

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "tier": self.tier,
            "contract_version": self.contract_version,
            "dimensions": [d.as_dict() for d in self.dimensions],
            "waiver": self.waiver,
            "failed": [d.name for d in self.dimensions if d.status == STATUS_FAIL],
            "review": [d.name for d in self.dimensions if d.status == STATUS_REVIEW],
        }


# Per-tier thresholds. `fail_below` is a hard floor; between `fail_below` and
# `review_below` the dimension enters REVIEW rather than passing silently.
#
# These are STARTING values, chosen against the two Buehler runs (the thin original
# and the curated re-run) and stated as such. They are not derived from a labelled
# corpus, because there isn't one yet — #64 requirement 6 asks for a small labelled
# audit set and shadow evaluation before automatic promotion, and that is exactly
# what should move these numbers. Until then the honest posture is: this layer
# reports and routes to review; it is not the arbiter of promotion.
DEFAULT_THRESHOLDS = {
    "standard": {
        "window_coverage":   {"fail_below": 0.50, "review_below": 0.80},
        "chunk_coverage":    {"fail_below": 0.40, "review_below": 0.75},
        "citation_support":  {"fail_below": 0.60, "review_below": 0.85},
        "citation_in_range": {"fail_below": 0.95, "review_below": 1.00},
        "endpoint_integrity": {"fail_below": 1.00, "review_below": 1.00},
        "predicate_concentration": {"higher_is_better": False,
                                    "fail_above": 0.75, "review_above": 0.60},
    },
    "thorough": {
        "window_coverage":   {"fail_below": 0.60, "review_below": 0.85},
        "chunk_coverage":    {"fail_below": 0.40, "review_below": 0.75},
        "citation_support":  {"fail_below": 0.60, "review_below": 0.85},
        "citation_in_range": {"fail_below": 0.95, "review_below": 1.00},
        "endpoint_integrity": {"fail_below": 1.00, "review_below": 1.00},
        "predicate_concentration": {"higher_is_better": False,
                                    "fail_above": 0.75, "review_above": 0.60},
        "discourse_variety": {"fail_below": 0.02, "review_below": 0.05},
    },
}


def _verdict(value: Optional[float], th: Optional[dict]) -> str:
    """Verdict for a dimension. `th["higher_is_better"]` defaults True.

    The direction has to be explicit. `predicate_concentration` is the one
    dimension where a HIGH number is the bad news, and an earlier version of this
    module applied the higher-is-better comparison to a concentration-shaped
    measure — which is how it ended up scoring a 213-fact extraction WORSE than a
    25-fact one on the real fixture.
    """
    if th is None:
        return STATUS_NOT_EVALUATED
    if value is None:
        # A dimension that could not be measured is NOT a pass. This is the
        # difference between "we checked and it was fine" and "we could not check",
        # and collapsing them is how an unmeasured thing starts reading as a good
        # one.
        return STATUS_REVIEW
    if th.get("higher_is_better", True):
        if value < th["fail_below"]:
            return STATUS_FAIL
        if value < th["review_below"]:
            return STATUS_REVIEW
        return STATUS_PASS
    if value > th["fail_above"]:
        return STATUS_FAIL
    if value > th["review_above"]:
        return STATUS_REVIEW
    return STATUS_PASS


def assess_extraction(
    *,
    merged: dict,
    chunks_by_index: dict,
    windows: Sequence[Any],
    tier: str,
    discourse_moves: Optional[Sequence[dict]] = None,
    identity_evidence: Optional[dict] = None,
    thresholds: Optional[dict] = None,
    waiver: Optional[dict] = None,
) -> QualityReport:
    """Assess one extraction. Deterministic; calls no model.

    `chunks_by_index` maps GLOBAL chunk_index -> chunk text. It is a mapping, not a
    list, deliberately: the extractor emits global chunk coordinates and a document's
    chunk indices need neither start at 0 nor be contiguous, so list position is not
    the same thing as chunk index. Treating them as the same would silently compare
    each fact against the wrong span and report a citation-support number that looks
    plausible and means nothing.

    `windows` carries the document's window plan, which supplies the coverage
    denominator.
    """
    th_all = thresholds or DEFAULT_THRESHOLDS
    th = th_all.get(tier) or {}
    facts = merged.get("facts") or []
    moves = list(discourse_moves or [])
    dims: list = []

    # ── 1. Window coverage ──────────────────────────────────────────────────
    # What fraction of the document's windows contributed at least one fact?
    # This is the check that catches the thin run: an extraction that only really
    # read the introduction scores low here no matter how many facts it produced,
    # because the denominator is the document, not the output.
    n_windows = len(windows) or 1
    covered = set()
    for f in facts:
        cr = f.get("chunk_range") or []
        if not cr:
            continue
        for w in windows:
            base = getattr(w, "chunk_index_base", None)
            idxs = getattr(w, "chunk_indices", None)
            if idxs and any(c in idxs for c in range(cr[0], (cr[-1] if len(cr) > 1 else cr[0]) + 1)):
                covered.add(getattr(w, "index", None))
            elif base is not None and cr[0] >= base:
                covered.add(getattr(w, "index", None))
    covered.discard(None)
    coverage = len(covered) / n_windows
    dims.append(Dimension(
        name="window_coverage", status=_verdict(coverage, th.get("window_coverage")),
        value=round(coverage, 4), threshold=(th.get("window_coverage") or {}).get("fail_below"),
        detail=f"{len(covered)} of {n_windows} windows produced at least one fact",
        evidence={"windows_total": n_windows, "windows_with_facts": len(covered),
                  "uncovered_windows": sorted(
                      {getattr(w, "index", i) for i, w in enumerate(windows)} - covered)[:20]},
    ))

    # ── 1b. Chunk coverage ─────────────────────────────────────────────────
    # What fraction of the document's CHUNKS is cited by at least one fact? This is
    # the depth metric, and on the real fixtures it is the one that actually
    # separates a thin extraction from a thorough one: the original Buehler run
    # scores 0.21 and the curated re-run 0.93.
    #
    # window_coverage alone could not tell them apart — both scored 1.0, because the
    # thin run touched all six windows, just shallowly. "Read every section" and
    # "read every section properly" are different claims and need different
    # measurements.
    #
    # Padding cannot inflate this: twenty invented facts all citing chunk 0 add one
    # chunk to the numerator, the same as one fact would, while the denominator is
    # the document and does not move.
    cited_chunks: set = set()
    for f in facts:
        cr = f.get("chunk_range") or []
        if not cr:
            continue
        lo_c, hi_c = cr[0], cr[-1]
        for ci in range(lo_c, hi_c + 1):
            if ci in chunks_by_index:
                cited_chunks.add(ci)
    chunk_cov = (len(cited_chunks) / len(chunks_by_index)) if chunks_by_index else None
    dims.append(Dimension(
        name="chunk_coverage", status=_verdict(chunk_cov, th.get("chunk_coverage")),
        value=round(chunk_cov, 4) if chunk_cov is not None else None,
        threshold=(th.get("chunk_coverage") or {}).get("fail_below"),
        detail=f"{len(cited_chunks)} of {len(chunks_by_index)} source chunks are cited "
               f"by at least one fact",
        evidence={"uncited_chunks": sorted(set(chunks_by_index) - cited_chunks)[:25]},
    ))

    # ── 2. Citation in-range ────────────────────────────────────────────────
    # Every fact claims a chunk_range. A range outside the document's chunk count
    # is a fabricated citation — cheap to detect, and it should essentially never
    # happen, hence a 0.95 floor rather than a lenient one.
    n_chunks = len(chunks_by_index)
    known = set(chunks_by_index)
    lo_bound = min(known) if known else 0
    hi_bound = max(known) if known else 0
    in_range = 0
    bad_ranges = []
    for f in facts:
        cr = f.get("chunk_range") or []
        lo = cr[0] if cr else None
        hi = cr[-1] if cr else None
        if lo is None or hi is None or lo < lo_bound or hi > hi_bound or lo > hi:
            bad_ranges.append({"fact_text": (f.get("fact_text") or "")[:120], "chunk_range": cr})
        else:
            in_range += 1
    cite_range = (in_range / len(facts)) if facts else None
    dims.append(Dimension(
        name="citation_in_range", status=_verdict(cite_range, th.get("citation_in_range")),
        value=round(cite_range, 4) if cite_range is not None else None,
        threshold=(th.get("citation_in_range") or {}).get("fail_below"),
        detail=f"{in_range} of {len(facts)} facts cite a chunk range inside the document "
               f"({lo_bound}..{hi_bound})",
        evidence={"out_of_range": bad_ranges[:10], "chunks_total": n_chunks},
    ))

    # ── 3. Citation support ─────────────────────────────────────────────────
    # Does the cited span actually contain the fact's subject/object language? This
    # is the dimension that makes padding counterproductive: an invented fact cites
    # a span that does not mention it, so adding it LOWERS the ratio.
    supported = 0
    unsupported = []
    measurable = 0
    for f in facts:
        cr = f.get("chunk_range") or []
        if not cr or n_chunks == 0:
            continue
        lo = max(lo_bound, cr[0])
        hi = min(hi_bound, cr[-1])
        if lo > hi:
            continue
        span = " ".join(chunks_by_index[c] for c in range(lo, hi + 1)
                        if c in chunks_by_index).lower()
        span_tokens = _content_tokens(span)
        claim_tokens = (_content_tokens(f.get("subject"))
                        | _content_tokens(f.get("object"))
                        | _content_tokens(f.get("object_literal")))
        if not claim_tokens:
            continue
        measurable += 1
        overlap = len(claim_tokens & span_tokens) / len(claim_tokens)
        # Half the distinctive endpoint language must appear in the span it cites.
        if overlap >= 0.5:
            supported += 1
        else:
            unsupported.append({
                "fact_text": (f.get("fact_text") or "")[:120],
                "chunk_range": cr, "token_overlap": round(overlap, 3),
            })
    cite_support = (supported / measurable) if measurable else None
    dims.append(Dimension(
        name="citation_support", status=_verdict(cite_support, th.get("citation_support")),
        value=round(cite_support, 4) if cite_support is not None else None,
        threshold=(th.get("citation_support") or {}).get("fail_below"),
        detail=f"{supported} of {measurable} facts have their endpoint language present in "
               f"the source span they cite",
        evidence={"unsupported_sample": unsupported[:10], "measurable_facts": measurable},
    ))

    # ── 4. Endpoint integrity ───────────────────────────────────────────────
    # Carried from the identity contract (#62) so the two layers report in one
    # place. Not re-derived here: re-deriving it would create a second opinion
    # about identity, and one of the two would eventually be wrong.
    ident_ok = None
    ident_detail = "identity contract not run for this extraction"
    if identity_evidence:
        ver = identity_evidence.get("verification") or {}
        if "binding_verified" in ver:
            ident_ok = 1.0 if ver["binding_verified"] else 0.0
            ident_detail = (
                f"{ver.get('checked_facts', 0)} facts checked, "
                f"{ver.get('offending_facts', 0)} endpoint(s) outside the frozen map")
        elif identity_evidence.get("warn_proceeded_unpinned"):
            ident_detail = "identity mode=warn: facts written UNPINNED, binding not verified"
    dims.append(Dimension(
        name="endpoint_integrity", status=_verdict(ident_ok, th.get("endpoint_integrity")),
        value=ident_ok, threshold=(th.get("endpoint_integrity") or {}).get("fail_below"),
        detail=ident_detail,
        evidence={"identity_mode": (identity_evidence or {}).get("mode")},
    ))

    # ── 5. Predicate concentration ──────────────────────────────────────────
    # The share of facts carried by the single most common predicate. HIGH is the
    # bad news: one relation repeated for volume.
    #
    # This replaces a "distinct predicates / facts" ratio, which was wrong by
    # construction and not merely mis-thresholded. Any distinct-over-total ratio
    # FALLS as a thorough extraction legitimately adds facts, so it penalised
    # exactly the behaviour it was meant to reward. Measured on the real Buehler
    # fixtures it scored the thin 25-fact run 0.48 and the curated 213-fact run
    # 0.108 — backwards. Concentration is scale-free: the thin run is 0.40 and the
    # curated run 0.49, both far from a degenerate single-predicate dump.
    if facts:
        pred_counts: dict = {}
        for f in facts:
            k = f.get("predicate")
            pred_counts[k] = pred_counts.get(k, 0) + 1
        concentration = max(pred_counts.values()) / len(facts)
        top_pred = max(pred_counts, key=lambda k: pred_counts[k])
    else:
        concentration, top_pred, pred_counts = None, None, {}
    dims.append(Dimension(
        name="predicate_concentration",
        status=_verdict(concentration, th.get("predicate_concentration")),
        value=round(concentration, 4) if concentration is not None else None,
        threshold=(th.get("predicate_concentration") or {}).get("fail_above"),
        detail=f"most common predicate {top_pred!r} holds "
               f"{pred_counts.get(top_pred, 0)} of {len(facts)} facts "
               f"({len(pred_counts)} distinct predicates)",
        evidence={"distinct_predicates": len(pred_counts),
                  "top_predicate": top_pred,
                  "higher_is_worse": True},
    ))

    # ── 6. Discourse variety (thorough only) ────────────────────────────────
    if "discourse_variety" in th:
        variety = (len({m.get("move_type") for m in moves}) / len(moves)) if moves else None
        dims.append(Dimension(
            name="discourse_variety", status=_verdict(variety, th.get("discourse_variety")),
            value=round(variety, 4) if variety is not None else None,
            threshold=th["discourse_variety"]["fail_below"],
            detail=f"{len({m.get('move_type') for m in moves})} distinct move types over "
                   f"{len(moves)} moves",
            evidence={"moves_total": len(moves)},
        ))

    # ── Roll-up ─────────────────────────────────────────────────────────────
    # Any FAIL fails. Otherwise any REVIEW routes to review. A waiver is recorded
    # ALONGSIDE the verdict and never overwrites it — the point of a waiver is that
    # someone decided to proceed anyway, which stays legible only if the thing they
    # decided about is still visible.
    if any(d.status == STATUS_FAIL for d in dims):
        status = STATUS_FAIL
    elif any(d.status == STATUS_REVIEW for d in dims):
        status = STATUS_REVIEW
    elif not th:
        status = STATUS_NOT_EVALUATED
    else:
        status = STATUS_PASS

    return QualityReport(status=status, dimensions=dims, tier=tier, waiver=waiver)
