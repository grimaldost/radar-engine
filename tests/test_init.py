"""`radar init` makes a data root; `radar add` writes the first entry into one.

THE RED PROOF, and what it is a proof OF. Until this module lands the engine can read a
catalogue and judge it but cannot produce one: every data root in existence was made by
hand, and the one script that ever seeded a catalogue (`seed.py`) was written against one
particular tree and retired to an attic. So the failing assertion is not a missing field
somewhere - it is that `stack_radar.init` does not exist and that `radar init` / `radar
add` are not verbs. Before the implementation every test here fails at import or on an
unknown-verb exit 2.

WHY THE WHOLE SEQUENCE IS ONE TEST rather than four. What `init` has to deliver is a
PATH - init, add, gate, render, in a directory that has never held a catalogue - and each
step's output is the next step's input. Asserting the four separately would pass on a tree
where `init` writes a marker the gate then refuses, which is exactly the failure the
path exists to catch: the engine's own fixtures (tests/conftest.py) hand-write a
minimal marker, so nothing else here ever reads a root that `init` produced.

THE SYNTHETIC `claude_home` is what keeps that path hermetic. The generated profile carries
DISCOVERED paths, never templated ones, so on a developer machine it points at
the real `~/.claude` and the gate would scan it. `--claude-home` overrides every profile's,
which is the flag the gate already has for exactly this reason, and it is pointed at an
empty directory: the invariant scan then reports that it had no data plane to read instead
of reading the operator's.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from stack_radar import cli, radar_lib

REPO = Path(__file__).resolve().parent.parent
SRC = Path(os.environ.get("RADAR_ENGINE_SRC") or (REPO / "src"))

MARKER = "radar.toml"

# The tree `init` produces. Read as a list rather than checked one assertion at a time so
# a partial implementation reports everything it did not write: the failure that matters
# is "the stranger's tree is incomplete", and finding that out one missing file per run is
# the slow way to learn it.
REQUIRED_ARTEFACTS = (
    "radar.toml",
    "environments/default.toml",
    "tools",
    ".gitignore",
    ".githooks",
    ".pre-commit-config.yaml",
    ".github/workflows/ci.yml",
    "CHANGELOG.md",
    "pyproject.toml",
)

# The keys a generated marker carries. `[publish]`, `[changelog]` and `[render]` are
# tables; the rest are scalars.
MARKER_SCALARS = ("schema", "title", "default_environment", "requires_framework")
MARKER_TABLES = ("invariant", "publish", "changelog", "render")


def child_env(**extra: str) -> dict[str, str]:
    """The parent environment with the root override removed.

    `RADAR_DATA_ROOT` is read by the resolver BEFORE it searches, so a machine that exports
    it would point `radar add` at a real catalogue and this whole module would write into
    the operator's tree instead of into tmp_path.
    """
    env = {**os.environ, **extra}
    env.pop("RADAR_DATA_ROOT", None)
    return env


def radar(*args: str, cwd: Path, timeout: int = 180) -> subprocess.CompletedProcess:
    """`radar <verb> ...` as a subprocess, reached through the launcher.

    Through `cli` and not the module directly, because half of what `init` adds IS the
    verb: an `init.py` that works when imported and is not dispatched to is a command
    nobody can run, and a test that imported the module would not notice.
    """
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-m", "stack_radar.cli", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=child_env(PYTHONPATH=str(SRC)),
    )


def outside(tmp_path: Path) -> Path:
    """A directory guaranteed to be no data root, with none above it.

    Guarded rather than assumed: if the temporary directory ever sat under a real
    catalogue, every "no marker" assertion here would be about something else.
    """
    assert not any((p / MARKER).is_file() for p in (tmp_path, *tmp_path.parents)), (
        f"a {MARKER} above {tmp_path} makes these proofs vacuous"
    )
    return tmp_path


@pytest.fixture
def fresh(tmp_path: Path) -> Path:
    """An initialised data root in a directory that has never held one."""
    target = outside(tmp_path) / "a-fresh-radar"
    done = radar("init", str(target), cwd=tmp_path)
    assert done.returncode == 0, f"radar init failed:\n{done.stdout}{done.stderr}"
    return target


@pytest.fixture
def empty_claude_home(tmp_path: Path) -> Path:
    home = tmp_path / "synthetic-claude-home"
    home.mkdir()
    return home


# A public entry that the gate has no finding about: `observe` needs no telemetry matcher
# and no pilot-exit block, and a URL is what `visibility = "public"` requires. The
# tool is a real published one so the entry is not a fiction, and it is one the engine's
# own documentation already uses as its neutral example.
PUBLIC_TOOL = (
    "add",
    "just",
    "--repo",
    "https://github.com/casey/just",
    "--axis",
    "python-engineering",
    "--ring",
    "observe",
    "--license",
    "CC0-1.0",
    "--visibility",
    "public",
    "--artifact",
    "cli",
    "--note",
    "Command runner; a candidate for the task lane",
    "--reason",
    "Entered the catalogue on the day it was first looked at",
)


class TestTheVerbsExist:
    """The launcher dispatches them. This is the assertion that is red first."""

    def test_init_and_add_are_verbs(self):
        assert {"init", "add"} <= set(cli.VERBS), sorted(cli.VERBS)

    def test_each_new_verb_has_a_summary_line(self):
        # The list of verbs is printed from SUMMARY; a verb with no line raises a KeyError
        # inside `usage()`, which turns `radar --help` into a traceback.
        assert {"init", "add"} <= set(cli.SUMMARY), sorted(cli.SUMMARY)

    def test_usage_names_init_as_the_verb_that_creates_a_root(self):
        # Every other verb finds a root or judges a directory. `init` makes one, and a help
        # text that lumps it in with the finders tells the reader to stand somewhere they
        # cannot stand yet.
        assert "init" in cli.usage()


class TestTheTreeInitWrites:
    def test_every_artefact_section_10_names_is_written(self, fresh: Path):
        missing = [rel for rel in REQUIRED_ARTEFACTS if not (fresh / rel).exists()]
        assert not missing, f"radar init wrote no {', '.join(missing)}"

    def test_the_hooks_directory_carries_both_hooks(self, fresh: Path):
        # `.githooks/` is only armed as a whole: `git config core.hooksPath` points at the
        # directory, so a missing commit-msg hook is a stage that silently never runs.
        for hook in ("pre-commit", "commit-msg"):
            assert (fresh / ".githooks" / hook).is_file(), hook

    def test_the_marker_carries_the_keys_of_sections_3_and_5(self, fresh: Path):
        marker = tomllib.loads((fresh / MARKER).read_text(encoding="utf-8"))
        assert [k for k in MARKER_SCALARS if not marker.get(k)] == []
        assert [k for k in MARKER_TABLES if not isinstance(marker.get(k), dict)] == []
        assert marker["schema"] == "radar-data/v1"

    def test_the_needle_is_the_data_root_s_directory_name(self, fresh: Path):
        marker = tomllib.loads((fresh / MARKER).read_text(encoding="utf-8"))
        assert marker["invariant"]["needles"] == [fresh.name]

    def test_the_needle_carries_the_reason_a_generic_name_is_weak(self, fresh: Path):
        """The needle is seeded with a comment, not just the key, and the comment says why.

        Without the key the scan runs on the absolute path alone and still prints `holds`,
        so the weakness is invisible in the output. A seeded needle nobody understands gets
        deleted the first time it is inconvenient; the comment is what argues against that.
        """
        text = (fresh / MARKER).read_text(encoding="utf-8")
        comments = "\n".join(ln for ln in text.splitlines() if ln.lstrip().startswith("#"))
        assert "holds" in comments, comments
        assert "absolute path" in comments.lower(), comments

    def test_the_boundary_note_is_seeded_with_the_engine_s_generic_default(self, fresh: Path):
        # render.py keeps this string as the fallback for a bank that declares none, and
        # says `radar init` seeds new banks with it. Two copies of one sentence drift, so
        # the seed has to BE that constant rather than resemble it.
        from stack_radar.render import DEFAULT_BOUNDARY_NOTE

        marker = tomllib.loads((fresh / MARKER).read_text(encoding="utf-8"))
        assert marker["render"]["boundary_note"] == DEFAULT_BOUNDARY_NOTE

    def test_the_marker_names_no_adr(self, fresh: Path):
        # A generated catalogue is a stranger's, and the decision records this engine was
        # designed against are not theirs to cite. The boundary note's default is under the
        # same rule. The prefix is spelled in two pieces so that this file does not carry
        # the form tests/test_self_contained.py forbids anywhere in the tracked tree.
        assert "AD" + "R-" not in (fresh / MARKER).read_text(encoding="utf-8")


class TestTheProfileIsDiscoveredNotTemplated:
    """`environments/default.toml` with DISCOVERED paths, never templated ones.

    The distinction is the whole reason `bootstrap.py` exists: a profile that ships
    `<home>/Documents` makes the operator turn placeholders into values by hand, and
    guessing a home directory is not a judgement - it is computable, so it is computed.
    """

    def test_the_profile_is_the_default_environment_the_marker_names(self, fresh: Path):
        marker = tomllib.loads((fresh / MARKER).read_text(encoding="utf-8"))
        profile = tomllib.loads(
            (fresh / "environments" / "default.toml").read_text(encoding="utf-8")
        )
        # A marker naming an environment that does not exist is the silent failure gate.py
        # already spends a check on for tool entries: invisible everywhere, reported nowhere.
        assert profile["name"] == marker["default_environment"]

    def test_no_written_path_is_a_placeholder(self, fresh: Path):
        paths = tomllib.loads(
            (fresh / "environments" / "default.toml").read_text(encoding="utf-8")
        ).get("paths", {})
        templated = {k: v for k, v in paths.items() if "{" in str(v) or "<" in str(v)}
        assert not templated, f"templated instead of discovered: {templated}"

    def test_a_path_that_was_not_found_is_commented_out_rather_than_guessed(self, tmp_path: Path):
        """A synthetic home with nothing in it: no key may be invented.

        Run against the function rather than the CLI, because the point is a home directory
        that does NOT hold the things being looked for - and the only home a subprocess has
        is the operator's real one, which does.
        """
        from stack_radar import init

        home = tmp_path / "bare-home"
        home.mkdir()
        discovered = init.discovered_paths(home)
        assert set(discovered) >= {"documents", "claude_home"}
        assert not [k for k, v in discovered.items() if v], discovered

        target = outside(tmp_path) / "bare-radar"
        init.create(target, home=home)
        profile = tomllib.loads(
            (target / "environments" / "default.toml").read_text(encoding="utf-8")
        )
        assert profile.get("paths", {}) == {}, (
            "a path that could not be discovered was written anyway - a profile carrying a "
            "guess fails later and somewhere else"
        )

    def test_the_starter_profile_writes_the_home_as_a_tilde(self, tmp_path: Path):
        """The profile lands in a TRACKED file, so it must not spell the account out.

        Every reader expands `~` back (`radar_lib.with_home`), so the file says the same
        thing and names nobody. A sibling the home is merely a prefix of is not this
        account's and stays as written.
        """
        from stack_radar import bootstrap, init

        home = tmp_path / "someone"
        (home / "Documents").mkdir(parents=True)
        (home / ".claude").mkdir()
        target = outside(tmp_path) / "tilde-radar"
        init.create(target, home=home)
        text = (target / "environments" / "default.toml").read_text(encoding="utf-8")
        assert home.as_posix() not in text, text
        paths = tomllib.loads(text)["paths"]
        assert paths["documents"] == "~/Documents"
        assert paths["claude_home"] == "~/.claude"

        sibling = {"documents": (tmp_path / "someone-old" / "Documents").as_posix()}
        assert bootstrap.collapse_home(sibling, home) == sibling


class TestTheGeneratedRootStandsOnItsOwn:
    def test_the_version_sites_of_the_new_root_agree(self, fresh: Path):
        assert radar_lib.version_site_errors(fresh) == []

    def test_the_root_satisfies_the_framework_range_it_declares(self, fresh: Path):
        """The engine that made the root satisfies the range the root declares.

        The range `init` writes is derived from the engine's own version, and the framework
        check reads that version (`framework_version()`). Until 0.2.0 the check read the new
        catalogue's `pyproject.toml` instead, and the two agreed only while a new catalogue
        started at the engine's version; this test went red when the engine moved to 0.2.0
        and the reading had not yet turned.
        """
        marker = tomllib.loads((fresh / MARKER).read_text(encoding="utf-8"))
        errs = radar_lib.framework_errors(
            radar_lib.framework_version(fresh), marker["requires_framework"]
        )
        assert errs == [], (
            f"the engine does not satisfy the `requires_framework` of a root it just made: {errs}"
        )

    def test_the_root_is_a_root_and_its_parent_is_not(self, fresh: Path):
        """`radar gate` needs a marker above, seen from the tree `init` just made.

        The same condition tests/test_data_root.py proves against a bare temporary
        directory, asserted here about the tree `init` just made - the marker it wrote is
        what a command standing inside finds, and standing one directory up finds nothing.
        """
        inside = radar("gate", "--no-data-plane", cwd=fresh)
        assert inside.returncode != 2, (
            f"the initialised root was not recognised as one:\n{inside.stdout}{inside.stderr}"
        )
        above = radar("gate", "--no-data-plane", cwd=fresh.parent)
        output = above.stdout + above.stderr
        assert above.returncode == 2, f"expected exit 2 above the root, got:\n{output}"
        assert MARKER in output, f"the failure never names {MARKER}:\n{output}"

    def test_init_refuses_to_overwrite_an_existing_root(self, fresh: Path):
        before = (fresh / MARKER).read_text(encoding="utf-8")
        done = radar("init", str(fresh), cwd=fresh.parent)
        assert done.returncode != 0, f"init overwrote a catalogue:\n{done.stdout}{done.stderr}"
        assert MARKER in done.stdout + done.stderr
        assert (fresh / MARKER).read_text(encoding="utf-8") == before


class TestAdd:
    def test_add_writes_an_entry_with_the_first_history(self, fresh: Path):
        done = radar(*PUBLIC_TOOL, cwd=fresh)
        assert done.returncode == 0, f"radar add failed:\n{done.stdout}{done.stderr}"
        entry = tomllib.loads((fresh / "tools" / "just.toml").read_text(encoding="utf-8"))
        assert entry["name"] == "just"
        assert radar_lib.validate(entry) == []
        history = entry["history"]
        assert len(history) == 1
        assert history[0]["ring"] == entry["ring"]
        assert history[0]["date"] and history[0]["reason"]

    def test_add_refuses_an_entry_validate_rejects_and_writes_nothing(self, fresh: Path):
        """A public entry whose `repo` is a local path: the URL rule, checked before writing.

        Validated BEFORE the file lands, not after: an entry written and then reported is
        an entry the next `git add -A` commits, and the gate would be the thing that found
        it - one commit too late.
        """
        args = list(PUBLIC_TOOL)
        args[args.index("--repo") + 1] = "../somewhere/on/this/machine"
        done = radar(*args, cwd=fresh)
        assert done.returncode != 0, f"a rejected entry was accepted:\n{done.stdout}"
        assert not (fresh / "tools" / "just.toml").exists()
        assert "repo" in done.stdout + done.stderr

    def test_add_refuses_what_the_gate_would_immediately_fail(self, fresh: Path):
        """`validate()` is not the whole gate, and a new catalogue has to pass the gate.

        `adopt` with a measurable artifact and no matcher is a FAIL from `gate.py`, not a
        schema error, so an `add` that only ran `validate()` would hand the operator a
        catalogue that fails on the very next command. Same predicate, imported, rather
        than a second opinion about the same rule.
        """
        args = list(PUBLIC_TOOL)
        args[args.index("--ring") + 1] = "adopt"
        done = radar(*args, cwd=fresh)
        output = done.stdout + done.stderr
        assert done.returncode != 0, f"an entry the gate fails was accepted:\n{output}"
        assert "telemetry" in output.lower(), output
        assert not (fresh / "tools" / "just.toml").exists()

    def test_add_refuses_to_clobber_an_existing_entry(self, fresh: Path):
        assert radar(*PUBLIC_TOOL, cwd=fresh).returncode == 0
        (fresh / "tools" / "just.toml").write_text("name = 'hand edited'\n", encoding="utf-8")
        done = radar(*PUBLIC_TOOL, cwd=fresh)
        assert done.returncode != 0, done.stdout
        assert (fresh / "tools" / "just.toml").read_text(encoding="utf-8") == (
            "name = 'hand edited'\n"
        )

    def test_add_needs_a_data_root(self, tmp_path: Path):
        done = radar(*PUBLIC_TOOL, cwd=outside(tmp_path))
        assert done.returncode == 2, done.stdout + done.stderr
        assert MARKER in done.stdout + done.stderr


class TestTheStrangerPath:
    """init, add, gate, render, end to end, in a directory that never held a catalogue.

    The one test that proves the four commands compose. Every other assertion here reads
    one artefact; this one runs the sequence a person who has never seen this estate would
    run, and the gate is the step that judges everything the three others wrote.
    """

    def test_init_add_gate_render(self, tmp_path: Path, empty_claude_home: Path):
        target = outside(tmp_path) / "a-strangers-radar"
        assert radar("init", str(target), cwd=tmp_path).returncode == 0
        assert radar(*PUBLIC_TOOL, cwd=target).returncode == 0

        gate = radar("gate", "--claude-home", str(empty_claude_home), cwd=target)
        output = gate.stdout + gate.stderr
        assert gate.returncode == 0, f"the gate bit on a freshly made catalogue:\n{output}"
        assert "0 FAIL" in output, output

        rendered = radar("render", cwd=target)
        assert rendered.returncode == 0, rendered.stdout + rendered.stderr
        readme = (target / "README.md").read_text(encoding="utf-8")
        assert readme.strip(), "render wrote an empty README.md"
        assert "just" in readme
        # The header names the commands that make and refresh the file, and a stranger has
        # the `radar` verbs - not the script files the engine ran as before it was packaged.
        assert "Generated by `radar render` - do not hand-edit." in readme
        assert "`radar snapshot` refreshes metrics -> `radar gate` applies" in readme
        assert "scripts/" not in readme
        assert (target / "docs" / "radar" / "index.html").read_text(encoding="utf-8").strip()

    def test_render_help_prints_the_options_and_writes_nothing(
        self, tmp_path: Path, empty_claude_home: Path
    ):
        # `render` had no parser and read `--public` straight from argv, so `radar render
        # --help` fell through to a full render: it rewrote the catalogue's tracked README.md
        # instead of printing help. Asking a command what it does must not run it.
        target = outside(tmp_path) / "help-radar"
        assert radar("init", str(target), cwd=tmp_path).returncode == 0
        assert radar(*PUBLIC_TOOL, cwd=target).returncode == 0
        artefacts = [target / "README.md", target / "docs" / "radar" / "index.html"]
        before = [p.read_bytes() if p.exists() else None for p in artefacts]

        shown = radar("render", "--help", cwd=target)

        assert shown.returncode == 0, shown.stdout + shown.stderr
        assert "--public" in shown.stdout, shown.stdout
        after = [p.read_bytes() if p.exists() else None for p in artefacts]
        assert after == before, "render --help wrote the catalogue's artefacts"

    def test_the_rendered_title_is_the_catalogue_s_own_and_not_the_engine_s(
        self, tmp_path: Path, empty_claude_home: Path
    ):
        # The title rule, seen from the stranger's side: an engine anyone can install must not
        # title their radar with any name but the one their own marker carries.
        target = outside(tmp_path) / "borrowed-name-radar"
        assert (
            radar("init", str(target), "--title", "Someone Else's Radar", cwd=tmp_path).returncode
            == 0
        )
        assert radar(*PUBLIC_TOOL, cwd=target).returncode == 0
        assert radar("render", cwd=target).returncode == 0
        readme = (target / "README.md").read_text(encoding="utf-8")
        assert readme.splitlines()[0] == "# Someone Else's Radar"
