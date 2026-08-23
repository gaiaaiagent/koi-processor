"""Every personal-KOI launchd job must point at a file that exists, in a checkout that holds still.

Written because `com.personal-koi.calendar-export` was dead for sixteen days and nothing
said so. Its `ProgramArguments` named
`~/projects/regenai/koi-processor/scripts/export_proton_ics.py` — the SHARED DEV checkout,
which CLAUDE.md already warns "sessions switch freely; assume it moves under you." A session
switched it to `darren/tenant-stamping-phase1`, the script stopped existing at that path, and
the job exited 2 every 900 seconds. It wrote 892 KB of the identical error into
`~/.calendars/export.log`, `launchctl list` showed the 2 the whole time, and the only
user-visible symptom was that Obsidian's calendar quietly stopped gaining events — 25 real
meetings missing by the time anyone looked, including one the next day.

This is the same shape as the chunk-embedder incident recorded in
`reference_koi_launchd_jobs_deploy_topology` (a job depending on unmerged branch state, dead
two days, 334 chunks silently unembedded). That one was fixed as an instance. The rule went
into CLAUDE.md and was never swept across the jobs that already existed, which is why a second
job was sitting in exactly the same state while the rule was being written.

So this asserts the RULE, over every job, rather than re-fixing the instance:

  1. every path-looking ProgramArgument resolves to a file that exists;
  2. no personal-KOI job loads code from the shared dev checkout.

Both checks read the plists that are actually INSTALLED in ~/Library/LaunchAgents, not the
copies committed here. A committed plist proves nothing about what launchd runs — the whole
defect was a divergence between the two.
"""

from __future__ import annotations

import plistlib
from pathlib import Path

import pytest

LAUNCH_AGENTS = Path.home() / "Library" / "LaunchAgents"

# The shared dev checkout. Case-insensitive on this filesystem, so both spellings reach the
# same directory and both must be caught — the dead plist used the lowercase one while
# CLAUDE.md documents the capitalised one, which is part of why it read as fine.
DEV_CHECKOUT_MARKERS = ("projects/regenai/koi-processor", "projects/RegenAI/koi-processor")

# Deployable checkouts: pinned to a branch, never switched. See CLAUDE.md DEPLOY TOPOLOGY.
STABLE_CHECKOUT_MARKERS = ("projects/koi-processor-runtime", "projects/koi-processor-service")


# TWO namespaces, not one. The jobs were named under both `com.personal-koi.*` and
# `com.personal.koi-*`, and globbing only the first silently excluded three installed jobs
# — including `com.personal.koi-repo-doc-sensors`, which was loading code from the shared
# dev checkout while this suite reported green. A guard that enumerates a subset does not
# fail; it passes, which is worse.
PLIST_GLOBS = ("com.personal-koi.*.plist", "com.personal.koi*.plist")


def installed_plists() -> list[Path]:
    if not LAUNCH_AGENTS.is_dir():
        return []
    found: set[Path] = set()
    for pattern in PLIST_GLOBS:
        found |= set(LAUNCH_AGENTS.glob(pattern))
    # `.bak-*`, `.retired-*` and `.corrupt-*` copies are not loaded by launchd.
    return sorted(p for p in found if p.suffix == ".plist")


def program_paths(plist_path: Path) -> list[str]:
    """Path-looking entries in ProgramArguments, plus Program if present.

    Only absolute paths are returned. A bare `python3` or a flag like `--apply` is not a
    path and must not be asserted about; the point is to check the things that can vanish.
    """
    try:
        data = plistlib.loads(plist_path.read_bytes())
    except Exception as exc:  # a plist launchd cannot parse is its own failure
        pytest.fail(f"{plist_path.name}: unparseable plist ({exc})")
    args = list(data.get("ProgramArguments") or [])
    if isinstance(data.get("Program"), str):
        args.append(data["Program"])
    return [a for a in args if isinstance(a, str) and a.startswith("/")]


def working_directory(plist_path: Path) -> list[str]:
    """`WorkingDirectory` is a code-location dependency too, and was not being read.

    `com.personal.koi-processor` names the dev checkout here. It is survivable only because
    its launcher happens to `cd` elsewhere — an accident of that script, not a property of
    the job. Asserting on ProgramArguments alone cannot see it.
    """
    try:
        data = plistlib.loads(plist_path.read_bytes())
    except Exception as exc:
        pytest.fail(f"{plist_path.name}: unparseable plist ({exc})")
    wd = data.get("WorkingDirectory")
    return [wd] if isinstance(wd, str) and wd.startswith("/") else []


def launched_script_bodies(plist_path: Path) -> list[tuple[str, str]]:
    """(script path, its text) for each launched shell script that exists.

    The dependency that actually bit is one level down from the plist:
    `repo-doc-sensors-start.sh` hardcodes KOI_PROCESSOR="$HOME/projects/RegenAI/koi-processor"
    and the plist names only the script. Widening the glob alone would still have missed it,
    because the offending path is not in the plist at all.
    """
    bodies = []
    for path in program_paths(plist_path):
        p = Path(path)
        if p.suffix in (".sh", ".bash", "") and p.is_file():
            try:
                bodies.append((path, p.read_text(errors="ignore")))
            except OSError:
                continue
    return bodies


@pytest.mark.skipif(not installed_plists(), reason="no personal-KOI LaunchAgents installed")
@pytest.mark.parametrize("plist", installed_plists(), ids=lambda p: p.stem)
def test_every_program_argument_exists(plist: Path) -> None:
    """A launchd job whose target file is gone exits nonzero forever and tells nobody.

    launchd records the exit code and moves on. There is no alert, no retry ceiling, no
    escalation — `launchctl list` shows the number to whoever thinks to look. Sixteen days
    is how long that took last time.
    """
    missing = [p for p in program_paths(plist) if not Path(p).exists()]
    assert not missing, (
        f"{plist.name} runs {missing}, which does not exist. "
        f"launchd will keep invoking it every interval and keep failing silently."
    )


@pytest.mark.skipif(not installed_plists(), reason="no personal-KOI LaunchAgents installed")
@pytest.mark.parametrize("plist", installed_plists(), ids=lambda p: p.stem)
def test_no_job_loads_code_from_the_shared_dev_checkout(plist: Path) -> None:
    """Existing today is not the property that matters; still existing tomorrow is.

    `test_every_program_argument_exists` passes for a dev-checkout path right up until
    someone switches the branch, so it cannot express this rule. The dev checkout is
    shared and expected to move; a deployable checkout is pinned and never switched.
    Depending on the first is the defect even while the file happens to be present.
    """
    offenders: list[str] = []
    for path in program_paths(plist) + working_directory(plist):
        if any(marker in path for marker in DEV_CHECKOUT_MARKERS):
            offenders.append(f"plist names {path}")
    # One level down: a launcher that hardcodes the dev checkout is the same dependency,
    # and is invisible to any check that reads only the plist.
    for script, body in launched_script_bodies(plist):
        for line_no, line in enumerate(body.splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            if any(marker in line for marker in DEV_CHECKOUT_MARKERS):
                offenders.append(f"{script}:{line_no} {line.strip()}")
    assert not offenders, (
        f"{plist.name} depends on the shared DEV checkout, which sessions branch-switch "
        f"freely:\n  " + "\n  ".join(offenders) +
        f"\nPoint it at one of {STABLE_CHECKOUT_MARKERS} instead "
        f"(see CLAUDE.md DEPLOY TOPOLOGY)."
    )


def test_the_rule_is_checked_against_something() -> None:
    """Guard the guard: with no plists found, both tests above skip and report green.

    An empty enumeration is indistinguishable from a clean one in pytest's output, and
    this suite exists precisely because a silent pass is what let the last one run for
    sixteen days. On this machine the jobs are installed, so finding none means the
    enumeration broke, not that the jobs are healthy.
    """
    if not LAUNCH_AGENTS.is_dir():
        pytest.skip("not a machine with LaunchAgents")
    assert installed_plists(), (
        f"no plist matching {PLIST_GLOBS} found under {LAUNCH_AGENTS}. Either every job was "
        f"uninstalled, or this check is looking in the wrong place and silently passing."
    )


def test_both_job_namespaces_are_enumerated() -> None:
    """The jobs live under two prefixes and the glob covered only one.

    That is not hypothetical drift: three jobs sat outside the enumeration, one of them
    loading code from the shared dev checkout, while every test above reported green. If a
    namespace ever empties out, this fails loudly rather than shrinking the measured
    surface in silence.
    """
    if not LAUNCH_AGENTS.is_dir():
        pytest.skip("not a machine with LaunchAgents")
    names = [p.name for p in installed_plists()]
    hyphen = [n for n in names if n.startswith("com.personal-koi.")]
    dotted = [n for n in names if n.startswith("com.personal.koi")]
    assert hyphen, "no com.personal-koi.* jobs enumerated"
    assert dotted, (
        "no com.personal.koi-* jobs enumerated. Three existed on 2026-08-23 "
        "(koi-processor, koi-knowledge-health, koi-repo-doc-sensors); if they are truly "
        "gone, delete this assertion deliberately rather than letting the glob shrink."
    )


# --------------------------------------------------------------------------------------
# A committed copy that has drifted from the installed one is worse than no copy: it reads
# as documentation of what runs, while what runs is something else. That divergence IS the
# original defect, so having introduced repo copies, assert they still agree.
# --------------------------------------------------------------------------------------

REPO_PLISTS = Path(__file__).resolve().parents[1] / "scripts"

# Fields that change what launchd actually does. Log paths and comments may differ
# harmlessly; these may not.
LOAD_BEARING = ("ProgramArguments", "Program", "WorkingDirectory", "KeepAlive",
                "StartInterval", "ThrottleInterval", "EnvironmentVariables")


def committed_plists() -> list[Path]:
    return sorted(REPO_PLISTS.glob("com.personal*.plist"))


@pytest.mark.skipif(not committed_plists(), reason="no plists committed under scripts/")
@pytest.mark.parametrize("committed", committed_plists(), ids=lambda p: p.stem)
def test_committed_plist_matches_the_installed_one(committed: Path) -> None:
    installed = LAUNCH_AGENTS / committed.name
    if not installed.exists():
        pytest.skip(f"{committed.name} is not installed on this machine")

    want = plistlib.loads(committed.read_bytes())
    got = plistlib.loads(installed.read_bytes())
    drifted = {
        key: {"committed": want.get(key), "installed": got.get(key)}
        for key in LOAD_BEARING
        if want.get(key) != got.get(key)
    }
    assert not drifted, (
        f"{committed.name} in scripts/ disagrees with the installed copy on "
        f"{sorted(drifted)}. The committed file then documents a job that is not the job "
        f"launchd runs — which is the exact divergence this suite exists to catch.\n"
        f"{drifted}"
    )


def test_the_repo_copy_of_the_sensor_launcher_matches_the_installed_one() -> None:
    """The launcher, not just the plist. The dev-checkout dependency lived in this file."""
    committed = REPO_PLISTS / "repo-doc-sensors-start.sh"
    installed = Path.home() / ".config" / "personal-koi" / "repo-doc-sensors-start.sh"
    if not installed.exists():
        pytest.skip("sensor launcher not installed on this machine")
    assert committed.exists(), "the launcher is no longer committed; the repo copy vanished"
    assert committed.read_text() == installed.read_text(), (
        f"{committed} differs from the installed {installed}. The hardcoded KOI_PROCESSOR "
        f"path that pointed the doc sensors at the shared dev checkout lived in this file, "
        f"so a stale committed copy hides exactly the class of defect it was added to pin."
    )
