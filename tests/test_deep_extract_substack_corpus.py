"""Make the hermetic scheduler regression part of normal pytest discovery."""

import subprocess
from pathlib import Path


def test_bounded_newest_first_substack_scheduler() -> None:
    script = Path(__file__).with_suffix(".sh")
    result = subprocess.run(
        ["/bin/bash", str(script)],
        cwd=script.parents[1],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "bounded and fail-closed" in result.stdout
