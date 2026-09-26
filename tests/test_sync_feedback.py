"""The index builder is resolved by a declared key, not by the tool's name.

Before this change `index_builder()` walked every loaded entry looking for one whose
`name` matched one hardcoded string, then trusted that ONE entry's
`INDEX_BUILDER_REL` constant unconditionally - a rename of either the entry or the
plugin that ships the builder would go unnoticed by every check in the series and only
raise the day the script actually ran. `[feedback].index_builder` replaces both: the
entry that ships the builder declares where it lives, relative to its own `worktree`,
and the resolver walks entries in load order (already alphabetical by file name,
`radar_lib.load_tools`) taking the first one whose declared path RESOLVES to a real
file - not the first that merely declares one. A dead key (the worktree moved, the
plugin renamed the script) has to fall through to the next candidate instead of ending
the search, because a WARN with no index regenerated is silent regression the moment
two own-ring entries both carry the key and only one is stale.

`TestCheckIsTheBoundary` is the onda-0 half of the acceptance criterion: `--check`
reports the pending targets and touches nothing, which is what makes it safe for this
series to run against a real inbox without becoming the ingestion ritual itself.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path, PureWindowsPath

from stack_radar import sync_feedback
from stack_radar.radar_lib import validate
from stack_radar.redact import EXTRA_HOMES_FILE

REPO = Path(__file__).resolve().parent.parent
SRC = Path(os.environ.get("RADAR_ENGINE_SRC") or (REPO / "src"))

BASE = {
    "name": "x",
    "repo": "https://example.invalid/x",
    "axis": "python-engineering",
    "ring": "own",
    "license": "MIT",
    "visibility": "private",
    "note": "n",
    "artifact": "cli",
    "history": [{"date": "2026-08-01", "ring": "own", "evidence": "e"}],
}


# --------------------------------------------------------------- index_builder()


class TestIndexBuilder:
    def test_resolves_the_builder_via_the_declared_key(self, tmp_path: Path):
        wt = tmp_path / "wt"
        rel = "scripts/build_index.py"
        (wt / "scripts").mkdir(parents=True)
        (wt / rel).write_text("# builder\n", encoding="utf-8")
        tools = [{"name": "z", "feedback": {"worktree": str(wt), "index_builder": rel}}]
        assert sync_feedback.index_builder(tools, {}) == wt / rel

    def test_a_dead_key_falls_through_to_the_entry_that_actually_resolves(self, tmp_path: Path):
        # "a primeira entrada cujo construtor declarado EXISTE - não a primeira que
        # declara": entry "a" sorts first and declares a path that resolves to
        # nothing (its worktree moved); entry "b" declares a real one. The resolver
        # must not stop at "a" and report the builder missing.
        rel = "tool/build.py"
        real_wt = tmp_path / "real"
        (real_wt / "tool").mkdir(parents=True)
        (real_wt / rel).write_text("# builder\n", encoding="utf-8")
        tools = [
            {"name": "a", "feedback": {"worktree": str(tmp_path / "gone"), "index_builder": rel}},
            {"name": "b", "feedback": {"worktree": str(real_wt), "index_builder": rel}},
        ]
        assert sync_feedback.index_builder(tools, {}) == real_wt / rel

    def test_no_entry_declaring_the_key_resolves_to_none(self):
        # The WARN path: every entry either has no [feedback], no index_builder, or a
        # dead one. Nothing here should raise - main() turns None into a printed WARN.
        tools = [
            {"name": "a", "feedback": {"dir": "feedback/a"}},
            {"name": "b"},
            {"name": "c", "feedback": {"worktree": "/nowhere", "index_builder": "x.py"}},
        ]
        assert sync_feedback.index_builder(tools, {}) is None

    def test_paths_placeholders_expand_before_the_worktree_is_probed(self, tmp_path: Path):
        (tmp_path / "b.py").write_text("# builder\n", encoding="utf-8")
        tools = [{"name": "a", "feedback": {"worktree": "{documents}", "index_builder": "b.py"}}]
        found = sync_feedback.index_builder(tools, {"documents": str(tmp_path)})
        assert found == tmp_path / "b.py"


# --------------------------------------------------------------- validate() knows the key


class TestValidateKnowsTheKey:
    def test_a_string_index_builder_validates(self):
        t = {
            **BASE,
            "feedback": {
                "dir": "feedback/x",
                "worktree": "/home/x",
                "index_builder": "scripts/build.py",
            },
        }
        assert validate(t) == []

    def test_a_non_string_index_builder_is_a_schema_error(self):
        t = {
            **BASE,
            "feedback": {
                "dir": "feedback/x",
                "worktree": "/home/x",
                "index_builder": ["scripts/build.py"],
            },
        }
        errs = validate(t)
        assert any("index_builder" in e for e in errs), errs


# --------------------------------------------------------------- --check is the boundary


TOOL_TOML = """\
name = "{name}"
repo = "https://example.invalid/{name}"
axis = "python-engineering"
ring = "own"
license = "MIT"
visibility = "private"
note = "n"
artifact = "cli"

[feedback]
dir = "feedback/{name}"

[[history]]
date = "2026-08-01"
ring = "own"
evidence = "e"
"""


def sync_root(dest: Path, *, pending: dict[str, list[str]]) -> Path:
    """A miniature radar with one inbox report per (target, body) in `pending`.

    Only what `sync_feedback` itself reads: no pyproject.toml (the marker declares
    no `requires_framework`, so `begin_command` never asks for one) and no
    tools.local/ (nothing here is machine-scoped). Returns the inbox root.
    """
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "radar.toml").write_text(
        'schema = "radar-data/v1"\ndefault_environment = "personal"\n', encoding="utf-8"
    )
    (dest / "tools").mkdir()
    (dest / "environments").mkdir()
    inbox = dest / "inbox"
    for name, bodies in pending.items():
        (dest / "tools" / f"{name}.toml").write_text(TOOL_TOML.format(name=name), encoding="utf-8")
        target = inbox / name
        target.mkdir(parents=True)
        for i, body in enumerate(bodies):
            (target / f"2026-09-12-report-{i}.md").write_text(body, encoding="utf-8")
    (dest / "environments" / "personal.toml").write_text(
        f'name = "personal"\nrings = ["own"]\n\n[paths]\nfeedback_root = "{inbox.as_posix()}"\n',
        encoding="utf-8",
    )
    return inbox


def run_sync(dest: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    env.pop("RADAR_DATA_ROOT", None)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(SRC), *([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])]
    )
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-m", "stack_radar.sync_feedback", *args],
        cwd=dest,
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )


def tracked_files(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()}


class TestCheckIsTheBoundary:
    def test_check_reports_the_pending_target_and_writes_nothing(self, tmp_path: Path):
        dest = tmp_path / "radar"
        sync_root(dest, pending={"alpha": ["um relatorio pendente\n"]})
        before = tracked_files(dest)
        done = run_sync(dest, "--env", "personal", "--check")
        after = tracked_files(dest)
        assert after == before, sorted(before ^ after)
        assert "alpha: would ingest 1" in done.stdout, done.stdout
        assert "1 target(s)" in done.stdout, done.stdout
        # --check with drift is a reported mismatch, not a crash.
        assert done.returncode == 1, done.stdout + done.stderr

    def test_check_with_nothing_pending_is_clean(self, tmp_path: Path):
        dest = tmp_path / "radar"
        sync_root(dest, pending={"alpha": []})
        done = run_sync(dest, "--env", "personal", "--check")
        assert done.returncode == 0, done.stdout + done.stderr
        assert "0 file(s) out of sync" in done.stdout, done.stdout

    def test_check_prints_the_warn_when_no_entry_resolves_a_builder(self, tmp_path: Path):
        # Every fixture entry here declares [feedback] with no index_builder at all, so
        # the WARN path this section keeps has to still fire, worded without naming any
        # one tool (the search is no longer scoped to a fixed name).
        dest = tmp_path / "radar"
        sync_root(dest, pending={"alpha": ["um relatorio\n"]})
        done = run_sync(dest, "--env", "personal", "--check")
        assert "[WARN] no tool entry resolves a [feedback].index_builder" in done.stdout, (
            done.stdout
        )


# --------------------------------------------------------------- redaction on the way in


class TestIngestMapsTheHomeOut:
    """A report lands in the archive with the home written as `~`, like a field report.

    The inbox is data plane, written by sessions on this machine, and a session writes
    whatever path it worked in. The archive is tracked, so the home has to be mapped on the
    way in - the same place the scoped names already were - or every synced report puts
    the account back into the catalogue that the backfill just cleaned. `Path.home` is
    patched and the command runs in this process, so the home is one the test invented.
    """

    def ingest_once(self, dest: Path, monkeypatch, capsys) -> tuple[int, str]:
        monkeypatch.chdir(dest)
        monkeypatch.delenv("RADAR_DATA_ROOT", raising=False)
        monkeypatch.setattr(sys, "argv", ["sync_feedback", "--env", "personal", "--no-mirror"])
        code = sync_feedback.main()
        return code, capsys.readouterr().out

    def setup(self, tmp_path: Path, monkeypatch) -> tuple[Path, Path, str]:
        home = tmp_path / "Users" / "someone"
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
        slug = re.sub(r"[^A-Za-z0-9]", "-", str(home))
        body = (
            f"Ran in `{home.as_posix()}/Documents/p` with inhouse-thing.\n"
            f"claude_home: {home / '.claude'}\n"
            f"session filed under {slug}-Documents-p\n"
        )
        dest = tmp_path / "radar"
        sync_root(dest, pending={"alpha": [body]})
        (dest / "tools.local").mkdir()
        (dest / "tools.local" / "inhouse-thing.toml").write_text(
            'name = "inhouse-thing"\nredact_as = "inhouse-lib"\n', encoding="utf-8"
        )
        return dest, home, body

    def test_the_archived_report_carries_neither_the_home_nor_the_scoped_name(
        self, tmp_path: Path, monkeypatch, capsys
    ):
        dest, home, _ = self.setup(tmp_path, monkeypatch)
        code, out = self.ingest_once(dest, monkeypatch, capsys)
        assert code == 0, out
        archived = (dest / "feedback" / "alpha" / "2026-09-12-report-0.md").read_text(
            encoding="utf-8"
        )
        assert archived == (
            "Ran in `~/Documents/p` with inhouse-lib.\n"
            f"claude_home: {Path('~') / '.claude'}\n"
            "session filed under ~-Documents-p\n"
        )

    def test_a_report_ingested_once_is_not_a_conflict_the_next_time(
        self, tmp_path: Path, monkeypatch, capsys
    ):
        # The comparison is against the REDACTED inbox copy. Against the raw one, every
        # report carrying the home would be reported as a conflict on every later run.
        dest, _, _ = self.setup(tmp_path, monkeypatch)
        self.ingest_once(dest, monkeypatch, capsys)
        code, out = self.ingest_once(dest, monkeypatch, capsys)
        assert code == 0, out
        assert "[FAIL]" not in out, out
        assert "0 conflict(s)" in out, out

    def test_the_inbox_keeps_the_real_paths(self, tmp_path: Path, monkeypatch, capsys):
        # The inbox is on the machine that owns the paths; only the archive travels.
        dest, _, body = self.setup(tmp_path, monkeypatch)
        self.ingest_once(dest, monkeypatch, capsys)
        inbox = dest / "inbox" / "alpha" / "2026-09-12-report-0.md"
        assert inbox.read_text(encoding="utf-8") == body


class TestIngestMapsADeclaredHomeOut:
    """A report carrying ANOTHER machine's home is archived with that mapped too.

    The inbox merges corpora across machines, so a report reaching it was not necessarily
    written here - and `Path.home()` answers with this account whatever the report says.
    The other homes are declared in the gitignored `redact.local.toml` at the data root,
    and ingest applies them through the same mapping.
    """

    # Invented, and joined onto its parent rather than written as one literal.
    OTHER = PureWindowsPath("C:/Users") / "other.account"

    def ingest_once(self, dest: Path, monkeypatch, capsys) -> tuple[int, str]:
        monkeypatch.chdir(dest)
        monkeypatch.delenv("RADAR_DATA_ROOT", raising=False)
        monkeypatch.setattr(sys, "argv", ["sync_feedback", "--env", "personal", "--no-mirror"])
        code = sync_feedback.main()
        return code, capsys.readouterr().out

    def setup(self, tmp_path: Path, monkeypatch, *, declare: bool) -> tuple[Path, str]:
        home = tmp_path / "Users" / "someone"
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
        body = f"Wrote it to `{self.OTHER.as_posix()}/Downloads`, from `{self.OTHER}\\work`.\n"
        dest = tmp_path / "radar"
        sync_root(dest, pending={"alpha": [body]})
        if declare:
            (dest / EXTRA_HOMES_FILE).write_text(
                f'homes = ["{self.OTHER.as_posix()}"]\n', encoding="utf-8"
            )
        return dest, body

    def archived(self, dest: Path) -> str:
        return (dest / "feedback" / "alpha" / "2026-09-12-report-0.md").read_text(encoding="utf-8")

    def test_the_declared_home_is_mapped_on_the_way_in(self, tmp_path: Path, monkeypatch, capsys):
        dest, _ = self.setup(tmp_path, monkeypatch, declare=True)
        code, out = self.ingest_once(dest, monkeypatch, capsys)
        assert code == 0, out
        assert self.archived(dest) == "Wrote it to `~/Downloads`, from `~\\work`.\n"

    def test_without_the_declaration_it_is_archived_as_written(
        self, tmp_path: Path, monkeypatch, capsys
    ):
        dest, body = self.setup(tmp_path, monkeypatch, declare=False)
        code, out = self.ingest_once(dest, monkeypatch, capsys)
        assert code == 0, out
        assert self.archived(dest) == body

    def test_a_report_ingested_once_is_not_a_conflict_the_next_time(
        self, tmp_path: Path, monkeypatch, capsys
    ):
        # The already-ingested comparison is against the redacted source, so the extra
        # homes have to be applied on both sides or every re-run reports a conflict.
        dest, _ = self.setup(tmp_path, monkeypatch, declare=True)
        self.ingest_once(dest, monkeypatch, capsys)
        code, out = self.ingest_once(dest, monkeypatch, capsys)
        assert code == 0, out
        assert "0 conflict(s)" in out, out
