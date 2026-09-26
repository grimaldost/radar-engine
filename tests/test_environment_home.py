"""A profile's `[paths]` may start with `~`, and every reader of a profile expands it.

A profile is tracked, and the paths it declares are true of one machine: written out in
full, each one carries the account name into the catalogue. `~` says the same thing
without naming anybody, but only if the code that reads the profile turns it back into
the home directory - otherwise `~/.claude` is a relative path to a folder called `~`.

Three places read a profile, and each is held to it here: `radar_lib.load_environments`
(every command that takes `--env`), `apply.load_profile` (which layers a machine-local
overlay on top) and `bootstrap.load_profile_raw` (which compares declared paths with the
discovered ones). The home comes from a patched `Path.home`, so it is one the test
invented, and the expansion is written with forward slashes - the spelling profiles
already use, so a path that was spelled out and one that was written with `~` load equal.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stack_radar import apply, bootstrap
from stack_radar.radar_lib import load_environment

MARKER = 'schema = "radar-data/v1"\ndefault_environment = "personal"\n'

PROFILE = """\
name = "personal"
rings = ["own"]

[paths]
claude_home = "~/.claude"
documents = "~/Documents"
bare = "~"
spelled_out = "/opt/elsewhere"
template = "{documents}/repo"
someone_else = "~other/x"
not_leading = "a/~/b"

[flags]
overlay = "personal.local.toml"
"""

OVERLAY = """\
[paths]
feedback_root = "~/.claude/feedback"
"""


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    fake = tmp_path / "Users" / "someone"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake))
    return fake


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    r = tmp_path / "radar"
    (r / "environments").mkdir(parents=True)
    (r / "radar.toml").write_text(MARKER, encoding="utf-8")
    (r / "environments" / "personal.toml").write_text(PROFILE, encoding="utf-8")
    (r / "environments" / "personal.local.toml").write_text(OVERLAY, encoding="utf-8")
    monkeypatch.chdir(r)
    monkeypatch.delenv("RADAR_DATA_ROOT", raising=False)
    return r


def test_load_environment_expands_a_leading_tilde(root: Path, home: Path):
    paths = load_environment("personal", root)["paths"]
    assert paths["claude_home"] == f"{home.as_posix()}/.claude"
    assert paths["documents"] == f"{home.as_posix()}/Documents"
    assert paths["bare"] == home.as_posix()


def test_values_that_do_not_start_with_the_home_are_left_alone(root: Path, home: Path):
    paths = load_environment("personal", root)["paths"]
    assert paths["spelled_out"] == "/opt/elsewhere"
    assert paths["template"] == "{documents}/repo"
    # `~other` is another account's home; guessing it would be wrong more often than not.
    assert paths["someone_else"] == "~other/x"
    assert paths["not_leading"] == "a/~/b"


def test_apply_expands_the_overlay_too(root: Path, home: Path):
    profile, _ = apply.load_profile("personal")
    assert profile["paths"]["feedback_root"] == f"{home.as_posix()}/.claude/feedback"
    assert profile["paths"]["claude_home"] == f"{home.as_posix()}/.claude"


def test_bootstrap_compares_the_expanded_path(root: Path, home: Path):
    profile = bootstrap.load_profile_raw(root, "personal")
    assert profile["paths"]["claude_home"] == f"{home.as_posix()}/.claude"


# ---------------------------------------------------------------- the writing side
#
# `~` in a profile is only half of it. `radar bootstrap --write` writes the starter
# profile into the TRACKED `environments/<env>.toml`, and it wrote the paths it discovered
# verbatim - so the documented way to bring a machine up produced a file a commit lane
# running `radar redact-backfill --check` refuses, because that maps the home out of every
# tracked text file. The writer has to emit the form the readers above already expand.


class TestTheStarterProfileWritesTilde:
    def discovered(self, home: Path) -> dict[str, str]:
        return {
            "documents": f"{home.as_posix()}/Documents",
            "claude_home": f"{home.as_posix()}/.claude",
            "feedback_root": f"{home.as_posix()}/.claude/feedback",
            "elsewhere": "/opt/shared",
            "missing": "",
        }

    def test_no_discovered_path_carries_the_home(self, home: Path):
        text = bootstrap.starter_profile("corp", self.discovered(home))
        assert home.as_posix() not in text, text
        assert str(home) not in text, text
        assert 'claude_home = "~/.claude"' in text, text
        assert 'documents = "~/Documents"' in text, text

    def test_a_path_outside_the_home_is_written_as_it_is(self, home: Path):
        text = bootstrap.starter_profile("corp", self.discovered(home))
        assert 'elsewhere = "/opt/shared"' in text, text
        # A key nothing was found for keeps its commented FILL IN line.
        assert '# missing = "FILL IN' in text, text

    def test_the_profile_it_writes_loads_back_to_the_discovered_paths(self, root: Path, home: Path):
        # The whole point: the tracked file names no account, and the readers give the
        # same absolute paths the discovery found.
        found = self.discovered(home)
        (root / "environments" / "corp.toml").write_text(
            bootstrap.starter_profile("corp", found), encoding="utf-8"
        )
        loaded = load_environment("corp", root)["paths"]
        for key, value in found.items():
            if value:
                assert loaded[key] == value, key

    def test_a_second_bootstrap_run_reports_no_drift(self, root: Path, home: Path):
        # `main()` compares the DECLARED paths with the discovered ones. If the profile it
        # wrote read back as a literal `~`, every path would look like drift and the
        # command would propose an overlay against the file it had just written.
        found = self.discovered(home)
        (root / "environments" / "corp.toml").write_text(
            bootstrap.starter_profile("corp", found), encoding="utf-8"
        )
        declared = bootstrap.load_profile_raw(root, "corp")["paths"]
        drift = {k: v for k, v in found.items() if v and declared.get(k, "") != v}
        assert drift == {}, drift

    def test_a_bare_home_becomes_a_bare_tilde(self, home: Path):
        text = bootstrap.starter_profile("corp", {"documents": home.as_posix()})
        assert 'documents = "~"' in text, text

    def test_a_sibling_of_the_home_is_not_collapsed(self, home: Path):
        # `<home>-old` is another directory, not this one. The same guard the redaction
        # applies: a name the home is a prefix of is somebody else's.
        sibling = f"{home.as_posix()}-old/Documents"
        text = bootstrap.starter_profile("corp", {"documents": sibling})
        assert f'documents = "{sibling}"' in text, text
