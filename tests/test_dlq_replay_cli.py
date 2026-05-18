"""DLQ replay CLI."""

import subprocess
import sys


def test_cli_help_shows_options():
    out = subprocess.check_output(
        [sys.executable, "-m", "api.dlq_replay", "--help"],
        cwd="/home/shawn/Workspace/KOI/koi-processor",
    ).decode()
    assert "--peer" in out
    assert "--limit" in out
    assert "--all" in out
    assert "--id" in out
