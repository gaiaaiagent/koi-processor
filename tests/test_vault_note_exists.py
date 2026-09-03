"""Unit tests for ``_vault_note_exists`` — the phantom-path guard primitive.

This pure-filesystem helper is the foundation of the phantom-path work on the
``feat/document-ingest-phase1`` branch: it is called by
``resolve_canonical_to_vault`` (to skip orphaned mappings) and
``/entities/mentioned-in`` (to report ``vault_note_exists``) so the vault never
gets a dangling wikilink for a registry entity whose note was deleted, relocated,
or never materialized (the ~113-orphan class observed 2026-06-02; also the
Jacob/MOVE37/Cascadia phantom resolutions observed 2026-06-10).

The two behaviours these tests lock in:

  1. **Existence is checked against the real FS** (with or without ``.md``), so a
     stored ``vault_path`` pointing at a missing note returns ``False``.
  2. **Basename drift is NOT silently "repaired" here** — ``Organizations/MOVE37``
     returns ``False`` even when ``Organizations/MOVE37XR.md`` exists. Relocation
     repair is the job of ``scripts/audit_orphan_mappings.py`` (which UPDATEs the
     stored path); this guard's contract is strictly "does THIS path exist".
  3. **Headless safety**: if the vault root is not mounted on this host, the guard
     returns ``True`` so a deploy that can't see the vault never strips legit links.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import api.personal_ingest_api as api  # noqa: E402


@pytest.fixture
def temp_vault(tmp_path, monkeypatch):
    """Point ``_VAULT_ROOT`` at a temp vault with a couple of notes."""
    (tmp_path / "People").mkdir()
    (tmp_path / "Organizations").mkdir()
    (tmp_path / "People" / "Jacob Sayles.md").write_text("# Jacob Sayles\n")
    (tmp_path / "Organizations" / "MOVE37XR.md").write_text("# MOVE37XR\n")
    monkeypatch.setattr(api, "_VAULT_ROOT", tmp_path)
    return tmp_path


def test_empty_path_is_false(temp_vault):
    assert api._vault_note_exists("") is False
    assert api._vault_note_exists(None) is False


def test_existing_note_with_md_suffix(temp_vault):
    assert api._vault_note_exists("People/Jacob Sayles.md") is True


def test_existing_note_without_md_suffix(temp_vault):
    # The stored vault_path / wikilink form usually has no extension.
    assert api._vault_note_exists("People/Jacob Sayles") is True


def test_missing_note_is_phantom(temp_vault):
    # The phantom case: registry resolved "Jacob" but People/Jacob.md doesn't exist.
    assert api._vault_note_exists("People/Jacob.md") is False
    assert api._vault_note_exists("People/Jacob") is False


def test_basename_drift_is_not_auto_repaired(temp_vault):
    # Stored phantom "Organizations/MOVE37" must report False even though the real
    # note lives at "Organizations/MOVE37XR.md" under a different basename.
    # Relocation repair belongs to audit_orphan_mappings.py, not this guard.
    assert api._vault_note_exists("Organizations/MOVE37") is False
    assert api._vault_note_exists("Organizations/MOVE37XR") is True


def test_vault_not_mounted_returns_true(tmp_path, monkeypatch):
    # Headless deploy: vault root isn't a directory → don't second-guess paths.
    monkeypatch.setattr(api, "_VAULT_ROOT", tmp_path / "does-not-exist")
    assert api._vault_note_exists("People/Anyone") is True
