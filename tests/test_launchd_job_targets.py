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


def installed_plists() -> list[Path]:
    if not LAUNCH_AGENTS.is_dir():
        return []
    return sorted(LAUNCH_AGENTS.glob("com.personal-koi.*.plist"))


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
    offenders = [
        p for p in program_paths(plist)
        if any(marker in p for marker in DEV_CHECKOUT_MARKERS)
    ]
    assert not offenders, (
        f"{plist.name} loads {offenders} from the shared DEV checkout, which sessions "
        f"branch-switch freely. Point it at one of {STABLE_CHECKOUT_MARKERS} instead "
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
        f"no com.personal-koi.*.plist found under {LAUNCH_AGENTS}. Either every job was "
        f"uninstalled, or this check is looking in the wrong place and silently passing."
    )
