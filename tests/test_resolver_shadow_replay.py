"""Replay evidence must be admissible: counted where it is valid, excluded where it is not.

WHY THIS FILE EXISTS
--------------------
Phase 6 needs 1,000 sampled attempts that reach the fuzzy guard boundary, across 13
production call sites. Live sampling produces roughly 6 a day and cannot reach the 10
callers with no organic traffic at all, so the gate would never fire and the
legacy/strict split would become permanent by default rather than by choice.

scripts/replay_resolver_shadow.py supplies that volume from the registry. But a replay
is only valid evidence about SOME of what the gate measures, and the failure mode is
subtle enough to pin:

  - divergence is a property of the two policies. Whoever produced the record, legacy
    and strict either agreed on that input or they did not. Replays count.
  - latency is a property of live traffic. In a replay the shadow comparison IS the
    workload, so shadow_overhead_ms/resolver_elapsed_ms approaches 1.0. Counting
    replays would trip exit 4 "overhead_failed" exactly when a replay large enough to
    satisfy the attempt bar was supplied — failing the gate for a reason that has
    nothing to do with the policy.
  - elapsed days is also a property of live traffic. A replay runs in one burst; if its
    timestamps counted, a big enough replay would silently satisfy a 7-day soak.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from api.resolver_shadow import start_attempt

REPO = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "analyze_resolver_shadow", REPO / "scripts" / "analyze_resolver_shadow.py"
)
analyze_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(analyze_mod)


def _record(**over):
    base = {
        "observed_at": "2026-08-23T12:00:00+00:00",
        "replay": False,
        "caller": "personal_ingest_api.ingest",
        "entity_type": "Person",
        "outcome_diverged": False,
        "candidate_divergences": 0,
        "shadow_overhead_ms": 0.01,
        "resolver_elapsed_ms": 10.0,
    }
    base.update(over)
    return base


def _analyze(records, **kw):
    params = dict(
        minimum_attempts=1,
        minimum_days=0.0,
        expected_callers=set(),
        fixture_callers=set(),
        max_overhead_ratio=0.05,
    )
    params.update(kw)
    return analyze_mod.analyze(records, **params)


# --------------------------------------------------------------------------------------

def test_the_attempt_carries_the_replay_flag_into_the_record() -> None:
    attempt = start_attempt(
        caller="c", engine="e", entity_type="Person", query_norm="q",
        active_policy="legacy", sampled_override=True, replay=True,
    )
    attempt.observe_candidate(
        uri="orn:x", score=0.99, tier="fuzzy",
        legacy_accepts=True, strict_accepts=True, elapsed_ns=1_000,
    )
    rec = attempt.finish(
        active_uri="orn:x", active_outcome="fuzzy",
        legacy_fallback="unresolved", strict_fallback="unresolved",
    )
    assert rec is not None and rec["replay"] is True


def test_live_attempts_default_to_not_replay() -> None:
    """A missing flag must read as live, so pre-existing records keep counting."""
    attempt = start_attempt(
        caller="c", engine="e", entity_type="Person", query_norm="q",
        active_policy="legacy", sampled_override=True,
    )
    attempt.observe_candidate(
        uri="orn:x", score=0.99, tier="fuzzy",
        legacy_accepts=True, strict_accepts=True, elapsed_ns=1_000,
    )
    rec = attempt.finish(
        active_uri="orn:x", active_outcome="fuzzy",
        legacy_fallback="unresolved", strict_fallback="unresolved",
    )
    assert rec["replay"] is False


def test_replay_overhead_cannot_fail_the_gate() -> None:
    """Positive control first: the same ratio on a LIVE record must fail."""
    hot = {"shadow_overhead_ms": 9.9, "resolver_elapsed_ms": 10.0}

    live_report, live_exit = _analyze([_record(**hot)])
    assert live_exit == 4 and live_report["verdict"] == "overhead_failed", (
        "a live record with ~1.0 overhead no longer fails — this test's control is gone"
    )

    replay_report, replay_exit = _analyze([_record(replay=True, **hot)])
    assert replay_exit == 0, (
        "a replay's overhead ratio failed the gate; replays measure the policy, "
        "not production latency"
    )
    assert replay_report["overhead_measured_on_live_records"] == 0
    assert replay_report["replay_attempts"] == 1


def test_an_empty_overhead_measurement_is_reported_not_passed_off_as_clean() -> None:
    report, _ = _analyze([_record(replay=True)])
    assert report["overhead_measured_on_live_records"] == 0, (
        "p95 0.0 with nothing measured must be distinguishable from a real 0.0 — "
        "'populated is not conformant', one level up"
    )


def test_replays_still_count_toward_attempts_callers_and_divergence() -> None:
    """The half a replay IS valid evidence for. Excluding it would defeat the purpose."""
    report, exit_code = _analyze(
        [_record(replay=True, caller="web_router.process", outcome_diverged=True)],
        expected_callers={"web_router.process"},
    )
    assert report["attempts"] == 1
    assert report["missing_callers"] == []
    assert report["outcome_divergences"] == 1
    assert exit_code == 3, "a divergence found by replay must still split the policy"


def test_a_replay_burst_cannot_masquerade_as_elapsed_soak() -> None:
    burst = [
        _record(replay=True, observed_at="2026-08-23T12:00:00+00:00"),
        _record(replay=True, observed_at="2026-08-23T12:00:30+00:00"),
    ]
    report, exit_code = _analyze(burst, minimum_days=7.0)
    assert report["observed_days"] == 0.0
    assert exit_code == 2 and report["verdict"] == "incomplete", (
        "a replay-only run must not satisfy a days requirement; waiving it has to be "
        "explicit via --minimum-days 0"
    )


# --------------------------------------------------------------------------------------

REPLAY = REPO / "scripts" / "replay_resolver_shadow.py"


def test_the_replay_excludes_the_row_it_is_replaying() -> None:
    """Production reaches fuzzy only when Tier 1 found no exact match. Leaving the row
    in would replay a decision that never happens and score a guaranteed 1.0."""
    src = REPLAY.read_text()
    assert 'c["fuseki_uri"] == target["fuseki_uri"]' in src, (
        "the replay no longer excludes the target row from its own candidate set"
    )


def test_the_replay_imports_the_guards_rather_than_restating_them() -> None:
    """A reimplemented policy would measure the harness, not the resolver."""
    src = REPLAY.read_text()
    for guard in (
        "passes_token_overlap_legacy",
        "passes_token_overlap_strict",
        "passes_person_name_guard",
        "passes_distinctive_token_check",
        "jaro_winkler_similarity",
    ):
        assert f"    {guard},\n" in src or f"from api.resolution_primitives import" in src, (
            f"{guard} is not imported from api.resolution_primitives"
        )
        assert f"def {guard}" not in src, f"{guard} is redefined in the replay harness"


def test_the_replay_does_not_pad_the_attempt_count() -> None:
    """Names that never reach the guard boundary say nothing about the policy."""
    src = REPLAY.read_text()
    assert "if shadow.candidates_observed == 0:" in src, (
        "attempts that reached no candidate are emitted, which inflates the 1,000-attempt "
        "bar with inputs that tested nothing"
    )
