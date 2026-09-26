"""The backfill maps the home directory out of every tracked text file, and `--check` answers
with its exit code.

`field_report` maps the home out of what it writes, and `sync_feedback` maps it out of what
it ingests. Neither does anything for the files a catalogue committed before they did, and
those are not only field reports: an archived feedback report quotes a path, a spec records
where a session ran, a profile declares its paths. So the home pass walks every tracked text
file, the same reach the scoped-name pass already had. A profile keeps working because the
loader expands `~` (tests/test_environment_home.py).

Each test builds a miniature catalogue that is also a git repository, because the backfill
enumerates what git tracks and nothing else, and runs the command in this process with
`Path.home` patched, so the home it maps is one the test invented.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path, PureWindowsPath

import pytest

from stack_radar import redact_backfill
from stack_radar.redact import EXTRA_HOMES_FILE

MARKER = 'schema = "radar-data/v1"\ndefault_environment = "personal"\n'
STEM = "2026-08-01_2026-08-31"

# The other machine's home: invented, and joined onto its parent rather than written as
# one literal, so this file stays clean under the publication rule it helps enforce.
OTHER_HOME = PureWindowsPath("C:/Users") / "other.account"


def slug_of(path: Path) -> str:
    """The project-folder name Claude Code gives a working directory."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


def mapped(text: str, home: Path) -> str:
    """`text` with each spelling of `home` written as `~`, spelled out by hand here so the
    oracle is not the function under test."""
    escaped = json.dumps(str(home))[1:-1]
    return (
        text.replace(escaped, "~")
        .replace(str(home), "~")
        .replace(home.as_posix(), "~")
        .replace(slug_of(home), "~")
    )


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    fake = tmp_path / "Users" / "someone"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake))
    return fake


def catalogue(root: Path, files: dict[str, str | bytes]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "radar.toml").write_text(MARKER, encoding="utf-8")
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(body if isinstance(body, bytes) else body.encode("utf-8"))
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)


def run(root: Path, monkeypatch: pytest.MonkeyPatch, capsys, *args: str) -> tuple[int, str]:
    monkeypatch.chdir(root)
    monkeypatch.delenv("RADAR_DATA_ROOT", raising=False)
    monkeypatch.setattr(sys, "argv", ["redact_backfill", *args])
    code = redact_backfill.main()
    return code, capsys.readouterr().out.replace("\\", "/")


def read(root: Path, rel: str) -> str:
    return (root / rel).read_bytes().decode("utf-8")


def home_files(home: Path) -> dict[str, str]:
    """One file per place a home turns up in a catalogue, each carrying it."""
    slug = slug_of(home)
    return {
        f"field/widget/{STEM}.json": (
            "{\n"
            f'  "claude_home": {json.dumps(str(home / ".claude"))},\n'
            f'  "slug": "{slug}-Documents-p",\n'
            '  "other": "C--Users-x-Documents-p"\n'
            "}\n"
        ),
        f"field/widget/{STEM}.md": f"claude_home: `{home / '.claude'}`\n| `{slug}` | 2 |\n",
        "feedback/widget/2026-08-02-note.md": (
            f"Ran from `{home.as_posix()}/Documents/p`, filed under `{slug}-Documents-p`.\n"
        ),
        "environments/elsewhere.toml": (
            f'[paths]\nclaude_home = "{home.as_posix()}/.claude"\n'
            f'documents = "{home.as_posix()}/Documents"\n'
        ),
        "docs/notes.md": f"The store sits at {home / 'store'}.\n",
    }


class TestHomePass:
    def test_every_tracked_text_file_loses_the_home_and_nothing_else(
        self, tmp_path, home, monkeypatch, capsys
    ):
        root = tmp_path / "radar"
        before = home_files(home)
        catalogue(root, before)
        code, out = run(root, monkeypatch, capsys)
        assert code == 0, out
        for rel, text in before.items():
            assert read(root, rel) == mapped(text, home), rel
            assert home.as_posix() not in read(root, rel), rel
        # A slug that is not the home's is somebody else's and stays.
        assert '"other": "C--Users-x-Documents-p"' in read(root, f"field/widget/{STEM}.json")
        assert '"slug": "~-Documents-p"' in read(root, f"field/widget/{STEM}.json")
        assert 'claude_home = "~/.claude"' in read(root, "environments/elsewhere.toml")

    def test_a_binary_file_keeps_its_bytes(self, tmp_path, home, monkeypatch, capsys):
        root = tmp_path / "radar"
        blob = b"\x89PNG\r\n" + str(home).encode("utf-8") + b"\x00"
        catalogue(root, {"docs/diagram.png": blob})
        code, out = run(root, monkeypatch, capsys)
        assert code == 0, out
        assert (root / "docs" / "diagram.png").read_bytes() == blob

    def test_an_untracked_file_is_not_touched(self, tmp_path, home, monkeypatch, capsys):
        root = tmp_path / "radar"
        catalogue(root, {"docs/tracked.md": "nothing here\n"})
        scratch = root / "docs" / "scratch.md"
        scratch.write_text(f"{home.as_posix()}/x\n", encoding="utf-8")
        run(root, monkeypatch, capsys)
        assert scratch.read_text(encoding="utf-8") == f"{home.as_posix()}/x\n"

    def test_the_output_names_each_file_it_mapped(self, tmp_path, home, monkeypatch, capsys):
        root = tmp_path / "radar"
        catalogue(root, home_files(home))
        _, out = run(root, monkeypatch, capsys)
        assert "[home] feedback/widget/2026-08-02-note.md" in out, out
        assert "[home] environments/elsewhere.toml" in out, out
        assert "the home directory out of 5 file(s)" in out, out

    def test_a_second_run_changes_nothing(self, tmp_path, home, monkeypatch, capsys):
        root = tmp_path / "radar"
        catalogue(root, home_files(home))
        run(root, monkeypatch, capsys)
        after = {rel: read(root, rel) for rel in home_files(home)}
        code, out = run(root, monkeypatch, capsys)
        assert code == 0, out
        assert {rel: read(root, rel) for rel in home_files(home)} == after


class TestCheckExitCode:
    """`--check` answers with its exit code, for either pass.

    It exited 0 whatever it found, so "would rewrite 3 file(s)" and "would rewrite 0
    file(s)" were the same shell answer, and a hook that ran it could never fail.
    """

    def test_a_home_outside_field_fails_the_check_and_writes_nothing(
        self, tmp_path, home, monkeypatch, capsys
    ):
        root = tmp_path / "radar"
        before = {"feedback/widget/2026-08-02-note.md": f"at {home.as_posix()}/p\n"}
        catalogue(root, before)
        code, out = run(root, monkeypatch, capsys, "--check")
        assert code == 1, out
        assert "feedback/widget/2026-08-02-note.md" in out, out
        for rel, text in before.items():
            assert read(root, rel) == text

    def test_the_check_passes_once_the_tree_is_clean(self, tmp_path, home, monkeypatch, capsys):
        root = tmp_path / "radar"
        catalogue(root, home_files(home))
        assert run(root, monkeypatch, capsys)[0] == 0
        code, out = run(root, monkeypatch, capsys, "--check")
        assert code == 0, out

    def test_a_pending_scoped_name_fails_the_check(self, tmp_path, home, monkeypatch, capsys):
        root = tmp_path / "radar"
        local = 'name = "inhouse-thing"\nredact_as = "inhouse-lib"\n'
        catalogue(
            root,
            {
                ".gitignore": "tools.local/\n",
                "tools.local/inhouse-thing.toml": local,
                "feedback/note.md": "ran inhouse-thing today\n",
            },
        )
        code, out = run(root, monkeypatch, capsys, "--check")
        assert code == 1, out
        assert read(root, "feedback/note.md") == "ran inhouse-thing today\n"
        assert run(root, monkeypatch, capsys)[0] == 0
        assert read(root, "feedback/note.md") == "ran inhouse-lib today\n"
        assert run(root, monkeypatch, capsys, "--check")[0] == 0


def declare_homes(root: Path, *homes: PureWindowsPath) -> None:
    """Write the gitignored declaration. AFTER the catalogue is staged, so the file is
    untracked exactly as it is on a real machine."""
    body = "homes = [\n" + "".join(f'  "{h.as_posix()}",\n' for h in homes) + "]\n"
    (root / EXTRA_HOMES_FILE).write_text(body, encoding="utf-8")


def other_machine_file(other: PureWindowsPath) -> str:
    """One report as another workstation wrote it: both path spellings of its home."""
    return (
        f"Wrote the extract to `{other.as_posix()}/Downloads`.\n"
        f"The repo sits under `{other}\\work\\repo`.\n"
    )


class TestDeclaredHomes:
    """A home this machine cannot find, because it is another machine's.

    A feedback report written elsewhere and synced here carries that machine's home. The
    backfill's own pass asks `Path.home()`, which answers with THIS account, so the other
    one survives every run. It is declared instead, in a gitignored file at the data root.
    """

    def test_a_declared_home_is_mapped_out_of_a_tracked_file(
        self, tmp_path, home, monkeypatch, capsys
    ):
        root = tmp_path / "radar"
        catalogue(root, {"feedback/widget/2026-08-02-note.md": other_machine_file(OTHER_HOME)})
        declare_homes(root, OTHER_HOME)
        code, out = run(root, monkeypatch, capsys)
        assert code == 0, out
        assert read(root, "feedback/widget/2026-08-02-note.md") == (
            "Wrote the extract to `~/Downloads`.\nThe repo sits under `~\\work\\repo`.\n"
        )

    def test_without_the_declaration_the_other_home_survives(
        self, tmp_path, home, monkeypatch, capsys
    ):
        # The proof that the declaration is what does it, rather than the machine's own
        # pass happening to reach the same string.
        root = tmp_path / "radar"
        before = other_machine_file(OTHER_HOME)
        catalogue(root, {"feedback/widget/2026-08-02-note.md": before})
        code, out = run(root, monkeypatch, capsys)
        assert code == 0, out
        assert read(root, "feedback/widget/2026-08-02-note.md") == before

    def test_the_check_fails_while_a_declared_home_is_still_tracked(
        self, tmp_path, home, monkeypatch, capsys
    ):
        root = tmp_path / "radar"
        before = other_machine_file(OTHER_HOME)
        catalogue(root, {"feedback/widget/2026-08-02-note.md": before})
        declare_homes(root, OTHER_HOME)
        code, out = run(root, monkeypatch, capsys, "--check")
        assert code == 1, out
        assert "[home] feedback/widget/2026-08-02-note.md" in out, out
        assert read(root, "feedback/widget/2026-08-02-note.md") == before

    def test_the_check_passes_once_the_declared_home_is_gone(
        self, tmp_path, home, monkeypatch, capsys
    ):
        root = tmp_path / "radar"
        catalogue(root, {"feedback/widget/2026-08-02-note.md": other_machine_file(OTHER_HOME)})
        declare_homes(root, OTHER_HOME)
        assert run(root, monkeypatch, capsys)[0] == 0
        code, out = run(root, monkeypatch, capsys, "--check")
        assert code == 0, out

    def test_this_machine_and_a_declared_home_are_both_mapped_in_one_run(
        self, tmp_path, home, monkeypatch, capsys
    ):
        root = tmp_path / "radar"
        body = f"here `{home.as_posix()}/p`, there `{OTHER_HOME.as_posix()}/p`\n"
        catalogue(root, {"feedback/widget/2026-08-02-note.md": body})
        declare_homes(root, OTHER_HOME)
        code, out = run(root, monkeypatch, capsys)
        assert code == 0, out
        assert read(root, "feedback/widget/2026-08-02-note.md") == "here `~/p`, there `~/p`\n"

    def test_the_declaration_file_is_not_itself_rewritten(
        self, tmp_path, home, monkeypatch, capsys
    ):
        # It is untracked, so the walk never reaches it - which is what keeps the one
        # place the account legitimately lives from being mapped out from under itself.
        root = tmp_path / "radar"
        catalogue(root, {"feedback/widget/2026-08-02-note.md": other_machine_file(OTHER_HOME)})
        declare_homes(root, OTHER_HOME)
        declaration = (root / EXTRA_HOMES_FILE).read_text(encoding="utf-8")
        run(root, monkeypatch, capsys)
        assert (root / EXTRA_HOMES_FILE).read_text(encoding="utf-8") == declaration
