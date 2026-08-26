"""scripts/vault_conflict_sweep.py's triage logic, isolated from the filesystem
and network calls it makes when run for real.

Built after three iCloud sync-conflict storms (240 files total, 2026-08-25/26)
were cleaned up manually with this exact body-only-diff method -- see the
Tooling Issues entries in Meta/Entity Resolution Issues.md for the incident.
This locks the logic in so a future edit can't silently regress it.
"""

import plistlib
from pathlib import Path

import pytest

from scripts.vault_conflict_sweep import (
    CONFLICT_RE,
    FALSE_POSITIVE_NAMES,
    find_conflict_files,
    split_frontmatter,
    triage,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


NOTE_WITH_FRONTMATTER = """---
"@type": Meeting
last_synced: 2026-08-25T00:00:00Z
---
# Title
Body line one.
Body line two.
"""

NOTE_NO_FRONTMATTER = "Just a body, no frontmatter block at all.\n"


def test_split_frontmatter_separates_correctly():
    fm, body = split_frontmatter(NOTE_WITH_FRONTMATTER)
    assert fm.startswith("---") and fm.rstrip().endswith("---")
    assert "last_synced" in fm
    assert "Body line one" in body
    assert "last_synced" not in body


def test_split_frontmatter_handles_missing_frontmatter():
    fm, body = split_frontmatter(NOTE_NO_FRONTMATTER)
    assert fm == ""
    assert body == NOTE_NO_FRONTMATTER


def test_conflict_regex_matches_the_icloud_pattern():
    assert CONFLICT_RE.match("Some Note (conflict 2026-08-25 07-37-31).md")
    assert CONFLICT_RE.match("2026-01-30 IndigenomicsAI Meeting (conflict 2026-08-25 08-41-01).md")
    assert not CONFLICT_RE.match("Some Note.md")
    assert not CONFLICT_RE.match("...round 2.md")  # the "125 vs 101" overcount bug this guards against


def test_cadap_false_positive_is_named_explicitly():
    # The literal case that motivated FALSE_POSITIVE_NAMES: a real note whose
    # own title contains "(Conflict" case-insensitively.
    assert "CADAP (Conflict Aftermath Digital Archive Project).md" in FALSE_POSITIVE_NAMES


@pytest.fixture
def vault(tmp_path):
    (tmp_path / "Meetings").mkdir()
    (tmp_path / "Projects").mkdir()
    return tmp_path


def _write(path, frontmatter_extra, body):
    path.write_text(f'---\n"@type": Meeting\n{frontmatter_extra}---\n{body}')


def test_triage_safe_when_body_identical_frontmatter_differs(vault):
    live = vault / "Meetings" / "Note.md"
    conflict = vault / "Meetings" / "Note (conflict 2026-08-25 07-00-00).md"
    _write(live, "last_synced: 2026-08-25T08:00:00Z\n", "Same body.\n")
    _write(conflict, "last_synced: 2026-08-24T01:00:00Z\n", "Same body.\n")
    verdict, detail = triage(str(conflict))
    assert verdict == "safe"
    assert detail is None


def test_triage_safe_when_conflict_body_is_subset_of_live(vault):
    live = vault / "Meetings" / "Note.md"
    conflict = vault / "Meetings" / "Note (conflict 2026-08-25 07-00-00).md"
    _write(live, "", "Line A.\nLine B.\nLine C (added later).\n")
    _write(conflict, "", "Line A.\nLine B.\n")
    verdict, detail = triage(str(conflict))
    assert verdict == "safe"


def test_triage_review_when_conflict_has_unique_body_content(vault):
    live = vault / "Meetings" / "Note.md"
    conflict = vault / "Meetings" / "Note (conflict 2026-08-25 07-00-00).md"
    _write(live, "", "Live content only.\n")
    _write(conflict, "", "This line exists nowhere in the live file.\n")
    verdict, detail = triage(str(conflict))
    assert verdict == "review"
    assert "This line exists nowhere in the live file." in detail


def test_triage_no_live_when_matching_file_is_missing(vault):
    conflict = vault / "Meetings" / "Orphan (conflict 2026-08-25 07-00-00).md"
    _write(conflict, "", "No matching live note.\n")
    verdict, detail = triage(str(conflict))
    assert verdict == "no_live"


def test_the_plist_cannot_storm():
    """Mirrors test_embedding_repair.py::test_the_plist_cannot_storm.

    That test exists because embedding-repair's plist had KeepAlive +
    StartInterval with no ThrottleInterval, and a 2026-08-12 external-provider
    failure turned an intended 109 runs into 3,040 in 9h06m. This job has the
    same shape (a periodic launchd job that could, in principle, gain
    KeepAlive later) -- pinning the same invariants here is what makes that
    regression testable at all: a plist that only ever existed in
    ~/Library/LaunchAgents, not committed to the repo, cannot be asserted on.

    Mutation: add KeepAlive back -> fails. Drop ThrottleInterval below
    StartInterval -> fails. Repoint at the dev checkout -> fails.
    """
    p = plistlib.loads(
        (REPO_ROOT / "scripts" / "com.personal-koi.vault-conflict-sweep.plist").read_bytes()
    )

    assert "KeepAlive" not in p, (
        "KeepAlive + StartInterval with no adequate ThrottleInterval is the exact "
        "shape that produced 3,040 crashed runs in 9h06m for embedding-repair on "
        "2026-08-12"
    )
    assert p["ThrottleInterval"] >= p["StartInterval"], (
        "ThrottleInterval must be >= StartInterval so launchd's 10s minimum "
        "runtime cannot govern if KeepAlive is ever re-added"
    )

    # Unlike embedding-repair, this job's ProgramArguments[0] is a launcher
    # script living under ~/.config/personal-koi/ (uncommitted, local-only --
    # same pattern as knowledge-health-run.sh), not a path inside the runtime
    # clone itself. The plist-level guarantee this test CAN see is
    # WorkingDirectory; the launcher script's own hardcoded
    # KOI_PROCESSOR="$HOME/projects/koi-processor-runtime" is the second,
    # unpinnable-from-here layer of the same guarantee.
    working_dir = p["WorkingDirectory"]
    assert "koi-processor-runtime" in working_dir, (
        f"job must run from the runtime clone, not {working_dir} -- a branch "
        f"switch in a dev checkout is exactly the risk this job was built to avoid"
    )
    assert "RegenAI" not in working_dir and "koi-processor-service" not in working_dir

    # Logs live under ~/.config, not a checkout, but must still never point at
    # a checkout path that could vanish under a branch switch.
    for k in ("StandardOutPath", "StandardErrorPath"):
        assert "RegenAI" not in p[k] and "koi-processor-service" not in p[k]


def test_find_conflict_files_excludes_cadap_and_finds_real_ones(vault):
    (vault / "Projects" / "CADAP (Conflict Aftermath Digital Archive Project).md").write_text("real note\n")
    conflict = vault / "Meetings" / "Note (conflict 2026-08-25 07-00-00).md"
    conflict.write_text("conflict copy\n")
    live = vault / "Meetings" / "Note.md"
    live.write_text("live copy\n")

    found = find_conflict_files(str(vault))
    assert str(conflict) in found
    assert not any("CADAP" in f for f in found)
    assert len(found) == 1
