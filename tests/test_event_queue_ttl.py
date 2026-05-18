"""Verify Spore-canon TTL (168 hours) for store-and-forward events."""

from api.event_queue import DEFAULT_TTL_HOURS, REMOTE_TTL_HOURS


def test_default_ttl_matches_spore_canon():
    """Spore canonizes 168h store-and-forward TTL."""
    assert DEFAULT_TTL_HOURS == 168


def test_remote_ttl_matches_spore_canon():
    """Remote-target events use same 168h budget."""
    assert REMOTE_TTL_HOURS == 168
