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
import subprocess
from pathlib import Path

import pytest

LAUNCH_AGENTS = Path.home() / "Library" / "LaunchAgents"

# The shared dev checkouts. Case-insensitive on this filesystem, so both spellings reach the
# same directory and both must be caught — the dead plist used the lowercase one while
# CLAUDE.md documents the capitalised one, which is part of why it read as fine.
#
# koi-sensors (email-sensor, email-watcher, proton-email-sensor) joined this list on
# 2026-08-25, once `koi-sensors-runtime` was established as its pinned-branch sibling
# (mirroring koi-processor-runtime). Before that it had the identical risk shape but no
# repoint target, so it was only reported via a warn-only test rather than hard-failed here
# — see git history for that test if the rationale is needed again.
DEV_CHECKOUT_MARKERS = (
    "projects/regenai/koi-processor", "projects/RegenAI/koi-processor",
    "projects/regenai/koi-sensors", "projects/RegenAI/koi-sensors",
)

# Deployable checkouts: pinned to a branch, never switched. See CLAUDE.md DEPLOY TOPOLOGY.
STABLE_CHECKOUT_MARKERS = (
    "projects/koi-processor-runtime", "projects/koi-processor-service",
    "projects/koi-sensors-runtime",
)


# TWO namespaces, not one. The jobs were named under both `com.personal-koi.*` and
# `com.personal.koi-*`, and globbing only the first silently excluded three installed jobs
# — including `com.personal.koi-repo-doc-sensors`, which was loading code from the shared
# dev checkout while this suite reported green. A guard that enumerates a subset does not
# fail; it passes, which is worse.
#
# `com.darren.*` joined on 2026-09-03, for the third instance of the same shape. The two
# namespaces above are the ones the KOI jobs were *named* under, but
# com.darren.claude-session-sensor writes session_chunks — the table migration 116
# constrains — and loads from projects/RegenAI/koi-sensors. It was invisible to this suite
# while the suite reported green over 17 plists, in a file whose own comment says a guard
# that enumerates a subset passes rather than fails. Enumerating it was the fix; the
# violation it exposes is tracked below rather than silently un-enumerated.
PLIST_GLOBS = (
    "com.personal-koi.*.plist", "com.personal.koi*.plist", "com.darren.*.plist",
)

# Known, ACCEPTED dev-checkout dependencies: (plist name, marker) -> why, and what would
# retire the entry. Deliberately keyed on the pair, so the exemption evaporates the moment
# the job points somewhere else — an exemption that survives a change of target is just a
# hole. Empty is the goal state.
KNOWN_DEV_CHECKOUT_EXCEPTIONS: dict[tuple[str, str], str] = {
    ("com.darren.claude-session-sensor.plist", "projects/RegenAI/koi-sensors"): (
        "Repointing is BLOCKED, not merely unscheduled: koi-sensors-runtime sits at "
        "pre-fix 0b5584e and has no venv for this sensor, so moving the plist there today "
        "would silently reinstate the empty-turn-pair chunk bug that commit f5c7f88 fixed "
        "and re-block migration 116. The fix is live only because this job runs from the "
        "dev checkout. Retire this entry once f5c7f88 is pushed, pulled into "
        "koi-sensors-runtime, and that checkout has a working venv."
    ),
    ("com.darren.sync-events-to-nuc.plist", "projects/RegenAI/koi-processor"): (
        "Different KIND of dependency, surfaced by widening the glob on 2026-09-03. This "
        "job does not EXECUTE code from the dev checkout — it runs `git log --since=yesterday` "
        "against it to build the morning brief's commit summary "
        "(darren-workflow/scripts/sync-events-to-nuc.sh:42). So it cannot die silently the "
        "way calendar-export did; the failure mode is a REPORTING one — the brief summarises "
        "whatever branch a session happened to leave checked out, so regen-prod commits can "
        "be missed and feature-branch commits reported as landed. Real, but it belongs to "
        "darren-workflow, not here. Retire this entry when that script reads "
        "koi-processor-service instead."
    ),
}


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
    def _exempt(marker: str) -> bool:
        return (plist.name, marker) in KNOWN_DEV_CHECKOUT_EXCEPTIONS

    offenders: list[str] = []
    for path in program_paths(plist) + working_directory(plist):
        if any(marker in path for marker in DEV_CHECKOUT_MARKERS if not _exempt(marker)):
            offenders.append(f"plist names {path}")
    # One level down: a launcher that hardcodes the dev checkout is the same dependency,
    # and is invisible to any check that reads only the plist.
    for script, body in launched_script_bodies(plist):
        for line_no, line in enumerate(body.splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            if any(marker in line for marker in DEV_CHECKOUT_MARKERS if not _exempt(marker)):
                offenders.append(f"{script}:{line_no} {line.strip()}")
    assert not offenders, (
        f"{plist.name} depends on the shared DEV checkout, which sessions branch-switch "
        f"freely:\n  " + "\n  ".join(offenders) +
        f"\nPoint it at one of {STABLE_CHECKOUT_MARKERS} instead "
        f"(see CLAUDE.md DEPLOY TOPOLOGY)."
    )


def installed_cron_lines() -> list[str]:
    """Lines from `crontab -l`, or [] if there is no crontab / cron is unavailable.

    A launchd-only guard is blind to a job installed via cron — koi-soak (soak-check.sh)
    runs every 2 hours from whatever directory was current when soak-cron-install.sh was
    last run, which was ~/projects/RegenAI/koi-processor (the dev checkout) at install time,
    and stays wherever that was regardless of later branch switches there.
    """
    try:
        result = subprocess.run(
            ["crontab", "-l"], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []  # no crontab installed for this user
    return result.stdout.splitlines()


def test_no_cron_job_loads_code_from_the_shared_dev_checkout() -> None:
    """Same rule as test_no_job_loads_code_from_the_shared_dev_checkout, applied to cron.

    Unlike koi-sensors above, koi-processor DOES have an established stable pair
    (koi-processor-service / koi-processor-runtime), so a violation here has a real
    repoint target and is hard-failed, not just reported.
    """
    lines = installed_cron_lines()
    if not lines:
        pytest.skip("no crontab installed for this user")
    offenders = [
        line for line in lines
        if any(marker in line for marker in DEV_CHECKOUT_MARKERS)
    ]
    assert not offenders, (
        "cron job(s) depend on the shared DEV checkout, which sessions branch-switch "
        "freely:\n  " + "\n  ".join(offenders) +
        f"\nPoint it at one of {STABLE_CHECKOUT_MARKERS} instead "
        f"(see CLAUDE.md DEPLOY TOPOLOGY). Fix scripts/federation/soak-cron-install.sh's "
        f"own PROJECT_DIR derivation too, or a reinstall will reintroduce this."
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
    assert [n for n in names if n.startswith("com.darren.")], (
        "no com.darren.* jobs enumerated. claude-session-sensor writes session_chunks and "
        "loaded from the shared dev checkout while this suite was green over 17 plists; "
        "if that namespace is truly empty now, delete this deliberately."
    )


def test_every_known_dev_checkout_exception_is_still_real() -> None:
    """An exemption that outlives its violation is a hole, not a record.

    KNOWN_DEV_CHECKOUT_EXCEPTIONS suppresses a specific (plist, marker) pair. If the job is
    repointed or deleted and the entry stays, it silently pre-authorises the NEXT job that
    lands on that name — which is the same subset-enumeration failure this file exists to
    catch, one level up. So each entry must still correspond to an observable violation.
    """
    if not LAUNCH_AGENTS.is_dir():
        pytest.skip("not a machine with LaunchAgents")
    installed = {p.name: p for p in installed_plists()}
    stale: list[str] = []
    for (name, marker) in KNOWN_DEV_CHECKOUT_EXCEPTIONS:
        plist = installed.get(name)
        if plist is None:
            stale.append(f"{name}: no longer installed")
            continue
        haystack = program_paths(plist) + working_directory(plist) + [
            line for _, body in launched_script_bodies(plist)
            for line in body.splitlines()
        ]
        if not any(marker in h for h in haystack):
            stale.append(f"{name}: no longer references {marker}")
    assert not stale, (
        "these exemptions no longer describe a real violation and must be DELETED:\n  "
        + "\n  ".join(stale)
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


# Operator scripts under ~/.config/personal-koi are machine-local: no repo, no test, no
# review. That is precisely why defects survive in them. Each entry here has already cost
# something:
#   repo-doc-sensors-start.sh  held the hardcoded KOI_PROCESSOR path that pointed the doc
#                              sensors at the shared dev checkout.
#   restart.sh                 waited 30 iterations of `curl --max-time 4; sleep 1`. Against
#                              a refused port curl fails in microseconds, so the budget was
#                              ~30s of wall clock while startup under a concurrent pg_dump
#                              takes 40-73s (measured three times on 2026-09-03). It printed
#                              ERROR on restarts that had SUCCEEDED — a check that cries
#                              wolf trains the operator to ignore it, so the next real
#                              failure goes unbelieved.
TRACKED_OPERATOR_SCRIPTS = ("repo-doc-sensors-start.sh", "restart.sh")


@pytest.mark.parametrize("script_name", TRACKED_OPERATOR_SCRIPTS)
def test_the_repo_copy_of_an_operator_script_matches_the_installed_one(script_name: str) -> None:
    """The launcher, not just the plist. These defects lived in the scripts, not the plists."""
    committed = REPO_PLISTS / script_name
    installed = Path.home() / ".config" / "personal-koi" / script_name
    if not installed.exists():
        pytest.skip(f"{script_name} is not installed on this machine")
    assert committed.exists(), (
        f"{script_name} is no longer committed under scripts/; the repo copy vanished and "
        f"the installed one is again the only copy, which is the state that let its last "
        f"defect survive unreviewed."
    )
    assert committed.read_text() == installed.read_text(), (
        f"{committed} differs from the installed {installed}. A committed copy that has "
        f"drifted is worse than none: it reads as documentation of what runs while "
        f"something else runs."
    )


# ---------------------------------------------------------------------------
# What a job is CONFIGURED to load vs. what a running process actually loaded.
#
# Everything above reads plists: configuration. None of it can tell you what an
# already-running process is executing, and that is the question every incident on
# 2026-09-01 turned on. Three agents, seven times in one day, read a plausible directory
# layout instead of asking the process — including reporting `~/koi-processor`
# (regen-prod @ d8fe44e, May 14, no venv, serving nothing) as "the NUC's state" while the
# service actually ran from `~/projects/RegenAI/koi-processor` on branch nuc-runtime.
# The conclusion drawn happened to be right; the evidence for it was not.
#
# The rule for that already lived in CLAUDE.md, with three worked incidents, and was read
# past by all three of us. Prose wired to attention decays as it becomes furniture. This
# is the same rule wired to a gate, so it fails on its own.
#
# Two traps, both of which turn a working guard back into no guard:
#   - Comparing an UNRESOLVED plist WorkingDirectory against a resolved /proc or lsof path
#     goes red on a correctly configured job the moment a symlink is involved. A test that
#     fails on healthy systems does not get fixed, it gets disabled.
#   - Enumerating nothing and passing. A quiet box must skip loudly, never report green.
# ---------------------------------------------------------------------------


def running_koi_jobs() -> list[tuple[str, int]]:
    """(label, pid) for every loaded personal-KOI job that currently has a live PID.

    `launchctl list` prints PID, last exit status, label. A '-' PID means loaded but not
    running, which is not a cwd question and is skipped here rather than failed — several
    of these jobs are intentionally interval-triggered.
    """
    try:
        out = subprocess.run(
            ["launchctl", "list"], capture_output=True, text=True, timeout=15
        ).stdout
    except Exception:
        return []
    jobs: list[tuple[str, int]] = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) != 3:
            continue
        pid, _status, label = parts
        if not (label.startswith("com.personal-koi.") or label.startswith("com.personal.koi")):
            continue
        if pid.isdigit():
            jobs.append((label, int(pid)))
    return jobs


def process_cwd(pid: int) -> str | None:
    """The cwd a process ACTUALLY has. /proc on Linux, lsof on macOS (there is no /proc)."""
    proc_cwd = Path("/proc") / str(pid) / "cwd"
    try:
        if proc_cwd.exists():
            return str(proc_cwd.resolve())
    except OSError:
        pass
    try:
        out = subprocess.run(
            ["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
            capture_output=True, text=True, timeout=15,
        ).stdout
    except Exception:
        return None
    for line in reversed(out.splitlines()):
        if line.startswith("n"):
            return line[1:]
    return None


def _real(path: str) -> str:
    """Resolve symlinks and ~ on BOTH sides before comparing.

    A plist may legitimately name a symlinked or ~-relative WorkingDirectory while /proc
    and lsof always report the real path. Comparing the raw strings fails on a healthy
    job, which is how a guard gets disabled instead of fixed.
    """
    try:
        return str(Path(path).expanduser().resolve())
    except OSError:
        return str(Path(path).expanduser())


@pytest.mark.skipif(not running_koi_jobs(), reason="no personal-KOI job is currently running")
@pytest.mark.parametrize("label,pid", running_koi_jobs(), ids=lambda v: str(v))
def test_running_process_cwd_matches_its_plist(label: str, pid: int) -> None:
    """A running job must be executing from the checkout its plist names.

    Configuration drift is caught above. This catches RUNTIME drift: a process that was
    started from somewhere else, or whose checkout moved under it after launch. Both are
    invisible to every plist-reading check in this file.
    """
    plist = LAUNCH_AGENTS / f"{label}.plist"
    if not plist.exists():
        pytest.skip(f"{label} is loaded but has no installed plist to compare against")
    declared = working_directory(plist)
    if not declared:
        pytest.skip(f"{label} declares no WorkingDirectory")
    actual = process_cwd(pid)
    if actual is None:
        pytest.skip(f"could not read cwd for pid {pid} ({label})")
    assert _real(actual) == _real(declared[0]), (
        f"{label} (pid {pid}) is RUNNING from {actual}, but its plist declares "
        f"WorkingDirectory={declared[0]}. The plist is not what this process loaded — "
        f"which is the divergence no configuration check can see."
    )


def test_the_runtime_check_is_checked_against_something() -> None:
    """A runtime check that enumerates nothing passes, which is worse than failing.

    Mirrors test_the_rule_is_checked_against_something above. If launchd reports live PIDs
    for personal-KOI jobs, this enumeration must find them; a parser that silently matches
    zero rows would let the assertion above report green on every machine forever.
    """
    try:
        out = subprocess.run(
            ["launchctl", "list"], capture_output=True, text=True, timeout=15
        ).stdout
    except Exception:
        pytest.skip("launchctl unavailable on this machine")
    live = [
        ln for ln in out.splitlines()
        if ("com.personal-koi." in ln or "com.personal.koi" in ln) and ln.split()[:1]
        and ln.split()[0].isdigit()
    ]
    if not live:
        pytest.skip("no personal-KOI job currently has a live PID")
    assert running_koi_jobs(), (
        f"launchctl reports {len(live)} running personal-KOI job(s) but running_koi_jobs() "
        f"enumerated none. The cwd assertion would then pass vacuously — the same "
        f"subset-enumeration failure that let com.personal.koi-repo-doc-sensors run from "
        f"the shared dev checkout while this suite reported green."
    )
