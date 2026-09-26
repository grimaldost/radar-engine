"""`versions.py`'s probe must find the GLOBAL tool, not the running project's own venv.

Under `uv run`, the interpreter that runs this very test suite has `VIRTUAL_ENV` set to
the project's own `.venv`, and that venv's `Scripts` (Windows) or `bin` (POSIX) directory
sits ahead of the rest of PATH - `uv run` puts it there so the project's pinned tools are
what the shell finds. `run()` resolved its probe with a bare `shutil.which(argv[0])`,
which reads that same PATH, so a tool the project pins as a dev dependency (`ruff`, here)
shadows the operator's globally-installed copy: the probe compares the PROJECT'S pinned
version against upstream and reports the global install as behind (or ahead) when the
global install was never read at all.

Reproduced here with two fake executables rather than the real `ruff`, so the test does
not depend on what is actually pinned in this project's `pyproject.toml` today, or on
`ruff` existing on the machine running CI at all.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from stack_radar import versions

VENV_BIN_NAME = "Scripts" if sys.platform == "win32" else "bin"


def _make_tool(directory: Path, name: str, version_line: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        (directory / f"{name}.cmd").write_text(
            f"@echo off\r\necho {version_line}\r\n", encoding="utf-8"
        )
    else:
        script = directory / name
        script.write_text(f"#!/bin/sh\necho '{version_line}'\n", encoding="utf-8")
        script.chmod(0o755)


def test_run_ignores_the_running_project_s_virtualenv(tmp_path, monkeypatch):
    venv = tmp_path / "project" / ".venv"
    venv_bin = venv / VENV_BIN_NAME
    global_dir = tmp_path / "global-tools"

    # The project-pinned copy sits AHEAD on PATH, the way `uv run` arranges it - and it
    # is the OLDER version, so a probe that reads it reports "behind" for a tool that is
    # actually current.
    _make_tool(venv_bin, "ruff", "ruff 0.1.0")
    _make_tool(global_dir, "ruff", "ruff 99.0.0")

    monkeypatch.setenv("VIRTUAL_ENV", str(venv))
    monkeypatch.setenv("PATH", os.pathsep.join([str(venv_bin), str(global_dir)]))

    out = versions.run("ruff --version")

    assert out is not None, "the probe found no `ruff` at all"
    assert "99.0.0" in out, f"expected the global tool's version, got: {out!r}"
    assert "0.1.0" not in out, f"the probe read the shadowing venv copy instead: {out!r}"


def test_run_still_finds_a_tool_with_no_virtualenv_active(tmp_path, monkeypatch):
    """The common case - no `VIRTUAL_ENV` at all - must keep working."""
    global_dir = tmp_path / "global-tools"
    _make_tool(global_dir, "ruff", "ruff 1.2.3")

    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setenv("PATH", str(global_dir))

    out = versions.run("ruff --version")

    assert out is not None
    assert "1.2.3" in out
