"""
Unit tests for Phase 5c eval contract logic.

Tests pure helper functions extracted from run_eval.py — no API calls needed.
"""

import json
import tempfile
from pathlib import Path

import pytest

# Import the helpers under test (avoid importing DeepEval by patching at module level)
import sys
from unittest.mock import MagicMock

# Stub deepeval before importing run_eval
deepeval_mock = MagicMock()
sys.modules["deepeval"] = deepeval_mock
sys.modules["deepeval.metrics"] = deepeval_mock
sys.modules["deepeval.test_case"] = deepeval_mock
deepeval_mock.FaithfulnessMetric = MagicMock
deepeval_mock.AnswerRelevancyMetric = MagicMock
deepeval_mock.ContextualRelevancyMetric = MagicMock
deepeval_mock.LLMTestCase = MagicMock

from tests.eval.run_eval import (
    check_resume_model_integrity,
    _orient_comparison,
    compute_aggregates,
    CANONICAL_EVAL_MODEL,
)


# ---------------------------------------------------------------------------
# Resume model integrity
# ---------------------------------------------------------------------------

class TestResumeModelIntegrity:
    """check_resume_model_integrity: fail-closed on mismatch or unknown."""

    def test_mismatch_aborts(self):
        """Checkpoint scored with gpt-4o-mini + --eval-model gpt-4.1 -> error."""
        checkpoint = {
            "eval_model": "gpt-4o-mini",
            "results": [{"id": "q1", "scores": {"context_relevancy": 0.5}}],
        }
        err = check_resume_model_integrity(checkpoint, "gpt-4.1")
        assert err is not None
        assert "gpt-4o-mini" in err
        assert "gpt-4.1" in err

    def test_legacy_scored_no_model_aborts(self):
        """Checkpoint with scored results but no eval_model field -> error (legacy fail-closed)."""
        checkpoint = {
            "results": [{"id": "q1", "scores": {"context_relevancy": 0.5}}],
        }
        err = check_resume_model_integrity(checkpoint, "gpt-4.1")
        assert err is not None
        assert "no eval_model metadata" in err

    def test_matching_model_proceeds(self):
        """Checkpoint with matching eval_model -> no error."""
        checkpoint = {
            "eval_model": "gpt-4.1",
            "results": [{"id": "q1", "scores": {"context_relevancy": 0.5}}],
        }
        err = check_resume_model_integrity(checkpoint, "gpt-4.1")
        assert err is None

    def test_fresh_checkpoint_no_scores_proceeds(self):
        """Checkpoint with no scored results + no eval_model -> proceeds (fresh start)."""
        checkpoint = {
            "results": [{"id": "q1", "scores": {}}],
        }
        err = check_resume_model_integrity(checkpoint, "gpt-4.1")
        assert err is None

    def test_empty_results_proceeds(self):
        """Checkpoint with empty results array -> proceeds."""
        checkpoint = {"results": []}
        err = check_resume_model_integrity(checkpoint, "gpt-4.1")
        assert err is None


# ---------------------------------------------------------------------------
# Gate orientation normalization
# ---------------------------------------------------------------------------

def _make_report(tag, planner, eval_model="gpt-4.1", results=None):
    """Helper to build a minimal report dict for orientation tests."""
    if results is None:
        results = [
            {"id": "q1", "normalized_category": "entity_definition",
             "scores": {"context_relevancy": 0.5, "answer_relevancy": 0.8, "faithfulness": 1.0}},
        ]
    return {
        "tag": tag,
        "planner_enabled": planner,
        "eval_model": eval_model,
        "results": results,
        "summary": {},
    }


class TestGateOrientation:
    """_orient_comparison: baseline=default, candidate=planner regardless of arg order."""

    def test_default_then_planner(self):
        """compare_reports(default, planner) -> baseline=default, candidate=planner."""
        default = _make_report("default", planner=False)
        planner = _make_report("planner", planner=True)
        ori = _orient_comparison(default, planner, "default.json", "planner.json")
        assert ori["baseline_path"] == "default.json"
        assert ori["candidate_path"] == "planner.json"
        assert "default" in ori["baseline_label"].lower() or "baseline" in ori["baseline_label"].lower()
        assert "planner" in ori["candidate_label"].lower() or "candidate" in ori["candidate_label"].lower()

    def test_planner_then_default_auto_normalized(self):
        """compare_reports(planner, default) -> same result (auto-normalized)."""
        default = _make_report("default", planner=False)
        planner = _make_report("planner", planner=True)
        ori = _orient_comparison(planner, default, "planner.json", "default.json")
        # Should be swapped: baseline=default, candidate=planner
        assert ori["baseline_path"] == "default.json"
        assert ori["candidate_path"] == "planner.json"

    def test_both_default_no_normalization(self):
        """compare_reports(default, default) -> arg order preserved, no planner."""
        a = _make_report("a", planner=False)
        b = _make_report("b", planner=False)
        ori = _orient_comparison(a, b, "a.json", "b.json")
        assert ori["baseline_path"] == "a.json"
        assert ori["candidate_path"] == "b.json"

    def test_both_planner_uses_arg_order(self):
        """compare_reports(planner_a, planner_b) -> arg order preserved with warning."""
        a = _make_report("a", planner=True)
        b = _make_report("b", planner=True)
        ori = _orient_comparison(a, b, "a.json", "b.json")
        assert ori["baseline_path"] == "a.json"
        assert ori["candidate_path"] == "b.json"


# ---------------------------------------------------------------------------
# Canonical verdict
# ---------------------------------------------------------------------------

class TestCanonicalVerdict:
    """compare_reports returns {gate_pass, canonical} with correct canonical flag."""

    def _write_report(self, tmpdir, filename, report):
        path = tmpdir / filename
        path.write_text(json.dumps(report))
        return str(path)

    def test_both_canonical_official_verdict(self, tmp_path):
        """Both reports gpt-4.1 -> canonical=True."""
        default = _make_report("default", planner=False, eval_model="gpt-4.1",
                               results=[{"id": "q1", "normalized_category": "entity_definition",
                                          "scores": {"context_relevancy": 0.5, "answer_relevancy": 0.8, "faithfulness": 1.0},
                                          "passed": True, "latency_s": 2.0}])
        planner = _make_report("planner", planner=True, eval_model="gpt-4.1",
                               results=[{"id": "q1", "normalized_category": "entity_definition",
                                          "scores": {"context_relevancy": 0.6, "answer_relevancy": 0.8, "faithfulness": 1.0},
                                          "passed": True, "latency_s": 3.0,
                                          "plan_trace": {"taxonomy": "entity_definition", "confidence": 0.9, "abstained": False, "fallback": False}}])
        planner["summary"] = {"planner": {"classifier_accuracy": 0.96, "fallback_rate": 0.0, "abstention_rate": 0.0, "avg_confidence": 0.9}}
        pa = self._write_report(tmp_path, "default.json", default)
        pb = self._write_report(tmp_path, "planner.json", planner)

        # Import compare_reports
        from tests.eval.run_eval import compare_reports
        result = compare_reports(pa, pb)
        assert result["canonical"] is True
        assert isinstance(result["gate_pass"], bool)

    def test_one_mini_informational(self, tmp_path):
        """One report gpt-4o-mini -> canonical=False (informational)."""
        default = _make_report("default", planner=False, eval_model="gpt-4o-mini",
                               results=[{"id": "q1", "normalized_category": "entity_definition",
                                          "scores": {"context_relevancy": 0.5, "answer_relevancy": 0.8, "faithfulness": 1.0},
                                          "passed": True, "latency_s": 2.0}])
        planner = _make_report("planner", planner=True, eval_model="gpt-4.1",
                               results=[{"id": "q1", "normalized_category": "entity_definition",
                                          "scores": {"context_relevancy": 0.6, "answer_relevancy": 0.8, "faithfulness": 1.0},
                                          "passed": True, "latency_s": 3.0,
                                          "plan_trace": {"taxonomy": "entity_definition", "confidence": 0.9, "abstained": False, "fallback": False}}])
        planner["summary"] = {"planner": {"classifier_accuracy": 0.96, "fallback_rate": 0.0, "abstention_rate": 0.0, "avg_confidence": 0.9}}
        pa = self._write_report(tmp_path, "default.json", default)
        pb = self._write_report(tmp_path, "planner.json", planner)

        from tests.eval.run_eval import compare_reports
        result = compare_reports(pa, pb)
        assert result["canonical"] is False

    def test_legacy_no_eval_model_informational(self, tmp_path):
        """Legacy report with no eval_model field -> canonical=False."""
        default = _make_report("default", planner=False, eval_model="gpt-4.1",
                               results=[{"id": "q1", "normalized_category": "entity_definition",
                                          "scores": {"context_relevancy": 0.5, "answer_relevancy": 0.8, "faithfulness": 1.0},
                                          "passed": True, "latency_s": 2.0}])
        # Legacy report — no eval_model key
        legacy = {"tag": "legacy", "planner_enabled": True,
                  "results": [{"id": "q1", "normalized_category": "entity_definition",
                               "scores": {"context_relevancy": 0.6, "answer_relevancy": 0.8, "faithfulness": 1.0},
                               "passed": True, "latency_s": 3.0,
                               "plan_trace": {"taxonomy": "entity_definition", "confidence": 0.9, "abstained": False, "fallback": False}}],
                  "summary": {"planner": {"classifier_accuracy": 0.96, "fallback_rate": 0.0, "abstention_rate": 0.0, "avg_confidence": 0.9}}}
        pa = self._write_report(tmp_path, "default.json", default)
        pb = self._write_report(tmp_path, "legacy.json", legacy)

        from tests.eval.run_eval import compare_reports
        result = compare_reports(pa, pb)
        assert result["canonical"] is False


# ---------------------------------------------------------------------------
# Partial metrics (--metrics flag)
# ---------------------------------------------------------------------------

class TestPartialMetrics:
    """compute_aggregates handles partial metric scores."""

    def test_cr_only_results(self):
        """Results with only context_relevancy scores produce valid aggregates."""
        results = [
            {"id": "q1", "normalized_category": "commitment_claim",
             "scores": {"context_relevancy": 0.5}, "passed": False},
            {"id": "q2", "normalized_category": "commitment_claim",
             "scores": {"context_relevancy": 0.7}, "passed": True},
        ]
        summary, scored_ids = compute_aggregates(results)
        assert summary["avg_scores"]["context_relevancy"] == 0.6
        assert summary["avg_scores"]["faithfulness"] is None
        assert summary["avg_scores"]["answer_relevancy"] is None
        assert len(scored_ids) == 2

    def test_mixed_metrics(self):
        """Results with a mix of present/absent metrics aggregate correctly."""
        results = [
            {"id": "q1", "normalized_category": "entity_definition",
             "scores": {"context_relevancy": 0.4, "faithfulness": 1.0}, "passed": False},
        ]
        summary, scored_ids = compute_aggregates(results)
        assert summary["avg_scores"]["context_relevancy"] == 0.4
        assert summary["avg_scores"]["faithfulness"] == 1.0
        assert summary["avg_scores"]["answer_relevancy"] is None


# ---------------------------------------------------------------------------
# Rescore validation
# ---------------------------------------------------------------------------

class TestRescoreValidation:
    """rescore_report validates retrieval_context presence."""

    def test_missing_retrieval_context_errors(self, tmp_path):
        """Rescore fails if source report lacks retrieval_context."""
        report = {
            "eval_model": "gpt-4.1-mini",
            "planner_enabled": True,
            "results": [
                {"id": "q1", "question": "test?", "scores": {"context_relevancy": 0.5},
                 "chat_response": {"answer": "test"}, "passed": False}
            ],
        }
        source = tmp_path / "no-context.json"
        source.write_text(json.dumps(report))

        from tests.eval.run_eval import rescore_report
        with pytest.raises(SystemExit):
            rescore_report(str(source), eval_model="gpt-4.1")


# ---------------------------------------------------------------------------
# Commitment claim planner steps (Phase 2)
# ---------------------------------------------------------------------------

class TestCommitmentClaimSteps:
    """Verify _commitment_claim_steps produces the B9b.1 plan (tightened)."""

    def test_step_count(self):
        from api.query_planner import _commitment_claim_steps
        steps = _commitment_claim_steps()
        assert len(steps) == 3, f"Expected 3 steps, got {len(steps)}"

    def test_text_search_top_k(self):
        from api.query_planner import _commitment_claim_steps
        from api.schemas.query_plan import RetrievalOp
        steps = _commitment_claim_steps()
        text_steps = [s for s in steps if s.op == RetrievalOp.TEXT_SEARCH]
        assert len(text_steps) == 1
        assert text_steps[0].params.get("top_k") == 8

    def test_structured_sql_max_reduced(self):
        from api.query_planner import _commitment_claim_steps
        from api.schemas.query_plan import RetrievalOp
        steps = _commitment_claim_steps()
        sql_steps = [s for s in steps if s.op == RetrievalOp.STRUCTURED_SQL]
        assert len(sql_steps) == 1
        assert sql_steps[0].budget.max_results == 5

    def test_entity_lookup_budget(self):
        from api.query_planner import _commitment_claim_steps
        from api.schemas.query_plan import RetrievalOp
        steps = _commitment_claim_steps()
        el_steps = [s for s in steps if s.op == RetrievalOp.ENTITY_LOOKUP]
        assert len(el_steps) == 1
        assert el_steps[0].budget.max_results == 5

    def test_no_relationship_traverse(self):
        """Relationship traverse removed — adds noise for conceptual questions."""
        from api.query_planner import _commitment_claim_steps
        from api.schemas.query_plan import RetrievalOp
        steps = _commitment_claim_steps()
        rel_steps = [s for s in steps if s.op == RetrievalOp.RELATIONSHIP_TRAVERSE]
        assert len(rel_steps) == 0

    def test_multi_query_enabled(self):
        from api.query_planner import _commitment_claim_steps
        from api.schemas.query_plan import RetrievalOp
        steps = _commitment_claim_steps()
        text_steps = [s for s in steps if s.op == RetrievalOp.TEXT_SEARCH]
        assert text_steps[0].params.get("multi_query") is True


# ---------------------------------------------------------------------------
# Relationship path steps (pre-Phase 3 — verify current state)
# ---------------------------------------------------------------------------

class TestRelationshipPathSteps:
    """Verify _relationship_path_steps current state (Phase 3 will modify if gate passes)."""

    def test_current_max_hops_is_2(self):
        """Before Phase 3, max_hops should be 2."""
        from api.query_planner import _relationship_path_steps
        from api.schemas.query_plan import RetrievalOp
        steps = _relationship_path_steps()
        rel_steps = [s for s in steps if s.op == RetrievalOp.RELATIONSHIP_TRAVERSE]
        assert len(rel_steps) == 1
        assert rel_steps[0].params.get("max_hops") == 2
