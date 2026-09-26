"""The tracked hooks have to be executable, or the lane is armed on paper only.

`git config core.hooksPath .githooks` is the one command CONTRIBUTING gives for arming
the commit lane, and the hooks it points at are tracked files rather than generated ones
precisely so a fresh clone needs nothing else. On Windows git runs them whatever their
mode; on Linux and macOS git SKIPS a hook that is not executable, and says nothing. A
clone there would report the lane armed while no check runs - the same "looking armed
while nothing runs" failure that `.pre-commit-config.yaml` records as the reason those
files exist at all.

Nothing else would notice: CI runs on ubuntu-latest but never commits, so it never
reaches a hook. The mode lives in the index, so that is what this reads - a working-tree
`os.access(X_OK)` is meaningless on Windows and would pass on a clone where the bit was
lost.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO = Path(os.environ.get("RADAR_DATA_ROOT") or Path(__file__).resolve().parent.parent)

EXECUTABLE = "100755"


def tracked_hooks() -> list[tuple[str, str]]:
    """`(mode, path)` for every tracked file under `.githooks/`."""
    out = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["git", "-C", str(REPO), "ls-files", "-s", ".githooks/"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    entries = []
    for line in out:
        meta, path = line.split("\t", 1)
        entries.append((meta.split()[0], path.strip()))
    return entries


def test_every_tracked_hook_is_executable_in_the_index():
    hooks = tracked_hooks()
    # Vacuity guard: an empty listing would pass the assertion below and prove nothing.
    assert {path for _mode, path in hooks} >= {
        ".githooks/pre-commit",
        ".githooks/commit-msg",
    }, hooks
    not_executable = [path for mode, path in hooks if mode != EXECUTABLE]
    assert not not_executable, (
        f"tracked as non-executable: {', '.join(not_executable)}. Git silently skips a "
        f"hook without the executable bit on POSIX, so `git config core.hooksPath "
        f".githooks` would arm nothing there. Fix with: git update-index --chmod=+x "
        f"{' '.join(not_executable)}"
    )
