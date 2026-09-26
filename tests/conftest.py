"""A synthetic radar root, so the field-report tests never read the real machine.

The commands resolve everything from the data root, which is the nearest directory above
the working directory carrying a `radar.toml`. So the cheapest hermetic fixture is a
whole miniature radar in tmp_path: the marker, a tools/ directory with the entries the
test declares, an environments/ profile pointing at a synthetic claude_home, and
transcripts written by hand. Every command runs with `cwd` at that root, so the search
finds the fixture's marker and nothing else. Nothing here touches the operator's real
transcripts or a tracked `field/` directory.

THE ENGINE IS NO LONGER COPIED IN, and its absence is the shape of the fixture rather
than a saving. While engine and catalogue were one tree, a miniature radar had to carry
copies of the modules under test because they were reached by path. An installed engine
is reached by name - `python -m stack_radar.<module>` - and a data root holds no code at
all, so the fixture now contains exactly what a real data root contains. A test that
needed the modules present inside the root would be testing an arrangement that no
longer occurs.

`RADAR_ENGINE_SRC` selects WHICH engine the subprocesses run. It is the successor to the
per-script `FIELD_REPORT_SRC` / `GATE_SRC` overrides and exists for the same reason -
the red proofs: point it at a `src/` extracted from an earlier commit and the same tests
run against the pre-change code. One variable rather than two because the modules import
each other within one package now, so mixing a pre-change `gate` with a post-change
`radar_lib` is not a state the engine can actually be in, and an override that could
produce it would prove a failure that no operator could ever meet.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
ENGINE_SRC = Path(os.environ.get("RADAR_ENGINE_SRC") or (REPO / "src"))

# A miniature radar is still a radar: it has the two version sites, so gate.py's
# repo-only half has something real to read. Kept minimal and in agreement, so a test
# that wants a version failure creates it explicitly rather than inheriting one.
MINI_PYPROJECT = """\
[project]
name = "mini-radar"
version = "0.2.0"
requires-python = ">=3.11"
"""
MINI_CHANGELOG = """\
# Changelog

## [0.2.0] - 2026-09-03

The fixture's own release.
"""

# A miniature radar needs the MARKER above all: without it a command run inside the
# fixture walks past tmp_path and either finds nothing (DataRootNotFound) or, worse on a
# machine where the temporary directory happens to live under a real root, finds that one.
#
# `[invariant].needles` is not padding either. The gate reads its needles from
# here, and a fixture with no needles would leave the invariant tests running against a
# scan that cannot fail - a whole class of test passing because the thing under test was
# switched off. It carries the same needle the tests plant in their synthetic data plane.
#
# `requires_framework` admits MINI_PYPROJECT's version and nothing else: the check runs on
# every command, so a range that excluded the fixture's own version would fail every test
# here with a message about versions.
MINI_MARKER = """\
schema = "radar-data/v1"
title = "mini-radar"
default_environment = "personal"
requires_framework = ">=0.2.0,<0.3.0"

[invariant]
needles = ["stack-radar"]
"""


def tool_use(tid: str, name: str, ts: str, **inp) -> dict:
    """One assistant record carrying a single tool_use block, as transcripts write it."""
    return {
        "type": "assistant",
        "timestamp": ts,
        "uuid": str(uuid.uuid4()),
        "message": {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}],
        },
    }


def tool_result(tid: str, ts: str, *, is_error: bool = False) -> dict:
    return {
        "type": "user",
        "timestamp": ts,
        "uuid": str(uuid.uuid4()),
        "message": {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": tid, "is_error": is_error, "content": "ok"}
            ],
        },
    }


def bash(tid: str, ts: str, command: str) -> dict:
    return tool_use(tid, "Bash", ts, command=command)


def note(ts: str, text: str = "nothing interesting") -> dict:
    """A record with no tool call - transcript filler, so a session has a time span."""
    return {
        "type": "user",
        "timestamp": ts,
        "uuid": str(uuid.uuid4()),
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
    }


class Radar:
    """A miniature radar root. Every path it returns is inside tmp_path."""

    def __init__(self, root: Path) -> None:
        self.root = root
        (root / "tools").mkdir(parents=True, exist_ok=True)
        (root / "environments").mkdir(exist_ok=True)
        self.home = root / "home"
        (self.home / "projects").mkdir(parents=True, exist_ok=True)
        self.archive_store = root / "archive-store"
        # The home directory the subprocesses see, or None to inherit the machine's. The
        # field report redacts the home out of what it writes, so a test of that has to
        # choose the home - otherwise it would be asserting about the real account, which
        # differs per machine and is absent on a runner.
        self.user_home: Path | None = None
        (root / "radar.toml").write_text(MINI_MARKER, encoding="utf-8")
        (root / "pyproject.toml").write_text(MINI_PYPROJECT, encoding="utf-8")
        (root / "CHANGELOG.md").write_text(MINI_CHANGELOG, encoding="utf-8")
        self.write_environment()

    # ------------------------------------------------------------------ inputs
    def write_environment(self, *, with_archive: bool = False) -> None:
        lines = [
            'name = "personal"',
            'rings = ["own", "adopt", "pilot"]',
            "",
            "[paths]",
            f'claude_home = "{self.home.as_posix()}"',
        ]
        if with_archive:
            lines.append(f'archive_store = "{self.archive_store.as_posix()}"')
        (self.root / "environments" / "personal.toml").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )

    def tool(self, name: str, toml: str) -> None:
        (self.root / "tools" / f"{name}.toml").write_text(toml, encoding="utf-8")

    def session(
        self,
        session_id: str,
        records: list[dict],
        *,
        slug: str = "C--Users-x-Documents-p",
        sub: str | None = None,
        mtime: float | None = None,
    ) -> Path:
        """Write one transcript. `sub` puts it in the session's sidechain subtree."""
        base = self.home / "projects" / slug
        path = base / f"{session_id}.jsonl" if sub is None else base / session_id / sub
        path.parent.mkdir(parents=True, exist_ok=True)
        # COMPACT, like the real thing. Claude Code writes `"tool_use_id":"toolu_..."`
        # with no spaces, and field_report's fast path for uninteresting lines is a
        # regex over exactly that shape - a fixture with pretty-printed separators
        # would exercise a code path the real corpus never takes.
        body = "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in records)
        path.write_text(body, encoding="utf-8")
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        return path

    def archive_rows(self, rows: list[dict], *, generated_at: str) -> None:
        """A synthetic copy of the archive's derived invocation table."""
        d = self.archive_store / "derived"
        d.mkdir(parents=True, exist_ok=True)
        (d / "invocations.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
        )
        (d / "derive.json").write_text(
            json.dumps({"generated_at": generated_at}) + "\n", encoding="utf-8"
        )

    def snapshot(self, name: str, entries: dict) -> Path:
        """Write `snapshots/<name>.json` by hand, the way `gate.py` reads it - never
        through `snapshot.py` itself, which hits the network. `latest_snapshot()` picks
        the lexicographically-last filename, so a test that wants ITS file read chooses
        one that sorts last among whatever else is in the directory."""
        d = self.root / "snapshots"
        d.mkdir(exist_ok=True)
        path = d / f"{name}.json"
        path.write_text(json.dumps(entries), encoding="utf-8")
        return path

    # ------------------------------------------------------------------ running
    def script(self, name: str, *args: str) -> subprocess.CompletedProcess:
        # `cwd` at the fixture's root is what makes the marker search land here, and
        # dropping RADAR_DATA_ROOT is what keeps it landing here: the resolver reads that
        # variable BEFORE it searches, so a machine that exports it would point every one
        # of these subprocesses at a real tree and the whole fixture would quietly stop
        # being hermetic.
        #
        # `-m`, so the module is reached by NAME and imports its siblings as a package.
        # `RADAR_ENGINE_SRC` is put on PYTHONPATH ahead of whatever is installed, which is
        # what lets a red proof run these same tests against an engine extracted from an
        # earlier commit without installing it.
        env = {**os.environ}
        env.pop("RADAR_DATA_ROOT", None)
        if self.user_home is not None:
            # Both variables, because the interpreter reads a different one per platform:
            # USERPROFILE on Windows, HOME everywhere else.
            env["HOME"] = env["USERPROFILE"] = str(self.user_home)
        env["PYTHONPATH"] = os.pathsep.join(
            [str(ENGINE_SRC), *([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])]
        )
        return subprocess.run(  # noqa: S603 - fixed argv, no shell
            [sys.executable, "-m", f"stack_radar.{name}", *args],
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=180,
            env=env,
        )

    def run(self, *args: str) -> subprocess.CompletedProcess:
        return self.script("field_report", *args)

    def gate(self, *args: str) -> subprocess.CompletedProcess:
        return self.script("gate", *args)

    def report(self, tool: str, *args: str) -> dict:
        """Run for one tool and return the JSON document it prints."""
        p = self.run(tool, "--json", *args)
        assert p.stdout.strip(), f"no JSON on stdout; stderr:\n{p.stderr}"
        return json.loads(p.stdout)


@pytest.fixture
def radar(tmp_path: Path) -> Radar:
    return Radar(tmp_path / "radar")
