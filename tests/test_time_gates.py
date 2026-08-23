from datetime import datetime, timedelta, timezone

import pytest

from scripts.check_intent_leak_observation import (
    ObservationError,
    classify_observation,
    parse_cutover,
)
from scripts.check_migration_112_evidence import (
    EvidenceError,
    classify_evidence,
    parse_start,
)


def test_intent_gate_cannot_pass_before_24_hours_even_with_zero_rows():
    cutover = datetime(2026, 8, 22, 20, 47, tzinfo=timezone.utc)
    assert classify_observation(
        database_now=cutover + timedelta(hours=23, minutes=59),
        deadline=cutover + timedelta(hours=24),
        nonconforming=0,
        orphaned=0,
        orphaned_proposals=0,
    ) == ("incomplete", 2)


def test_intent_gate_passes_elapsed_clean_window_and_fails_bad_provenance():
    deadline = datetime(2026, 8, 23, tzinfo=timezone.utc)
    now = deadline + timedelta(seconds=1)
    assert classify_observation(
        database_now=now, deadline=deadline, nonconforming=0, orphaned=0,
        orphaned_proposals=0,
    ) == ("pass", 0)
    assert classify_observation(
        database_now=now, deadline=deadline, nonconforming=1, orphaned=1,
        orphaned_proposals=0,
    ) == ("fail", 3)


def test_intent_gate_fails_on_orphaned_proposals_alone():
    """The surface the gate was blind to until 2026-08-23.

    As written it counted entity_registry only, so it would have reported a clean pass
    while 256 orphaned intent_match_proposals rows sat in the live database. Zero on the
    two original counters must NOT be sufficient on its own.
    """
    deadline = datetime(2026, 8, 23, tzinfo=timezone.utc)
    now = deadline + timedelta(seconds=1)
    assert classify_observation(
        database_now=now, deadline=deadline, nonconforming=0, orphaned=0,
        orphaned_proposals=256,
    ) == ("fail", 3)


def test_orphaned_proposals_is_required_not_defaulted():
    """A default of 0 would let an un-updated caller pass while never measuring it.

    That is the failure this whole class keeps taking: the check runs, reports zero, and
    the zero means 'not looked at'. Force every caller to supply the value.
    """
    deadline = datetime(2026, 8, 23, tzinfo=timezone.utc)
    with pytest.raises(TypeError):
        classify_observation(
            database_now=deadline + timedelta(seconds=1),
            deadline=deadline, nonconforming=0, orphaned=0,
        )


def test_migration_112_gate_requires_time_population_and_complete_tiers():
    deadline = datetime(2026, 8, 29, tzinfo=timezone.utc)
    assert classify_evidence(
        database_now=deadline - timedelta(seconds=1),
        deadline=deadline,
        observed_rows=100,
        null_rows=0,
    ) == ("incomplete_soak", 2)
    assert classify_evidence(
        database_now=deadline, deadline=deadline, observed_rows=0, null_rows=0
    ) == ("decorative_instrumentation", 3)
    assert classify_evidence(
        database_now=deadline, deadline=deadline, observed_rows=100, null_rows=4
    ) == ("producer_coverage_failed", 4)
    assert classify_evidence(
        database_now=deadline, deadline=deadline, observed_rows=100, null_rows=0
    ) == ("migration_112_evidence_ready", 0)


def test_time_inputs_must_include_explicit_offsets():
    with pytest.raises(ObservationError, match="explicit UTC offset"):
        parse_cutover("2026-08-22T13:47:00")
    with pytest.raises(EvidenceError, match="explicit UTC offset"):
        parse_start("2026-08-22T10:05:47")
