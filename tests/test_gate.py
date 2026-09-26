"""The gate must say what it did NOT check.

Two of the gate's checks need a machine rather than a repository: the invariant
scan needs a data plane to grep, and the scope-leak scan needs the gitignored
`tools.local/` entries whose names it searches for. On a CI runner neither exists -
`environments/personal.toml` points at Windows paths that are absent there by
construction - and the pre-change code printed, in substance,

    [NOTE] invariant: holds - 0 data-plane files scanned, 0 references

which is the wording of a real pass produced by scanning nothing. A gate that cannot
distinguish "checked and clean" from "had nothing to check" will eventually check
nothing and report success, which is the same decay an unverified
invariant undergoes.

These run gate.py as a subprocess against the miniature radar in tmp_path (conftest.py),
so they exercise argument parsing and the whole main() rather than a helper a refactor
could route around.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

ABSENT = "C:/definitely/absent"

VALID = """\
name = "ruff"
repo = "https://github.com/astral-sh/ruff"
axis = "python-engineering"
artifact = "cli"
ring = "adopt"
license = "MIT"
visibility = "public"
note = "linter"

[telemetry]
match_command = ["ruff"]
since = "2026-08-01"

[[history]]
date = "2026-08-01"
ring = "adopt"
evidence = "e"
"""

# An `own` entry whose checkout is declared the way every real one is: with the profile's
# `{documents}` placeholder, because one entry has to resolve on every machine. Private,
# like every real own-ring entry with a local checkout - a public entry's `repo` has to
# be a URL, and a checkout path is not one.
OWN = """\
name = "widget"
repo = "{documents}/widget"
axis = "python-engineering"
artifact = "cli"
ring = "own"
license = "MIT"
visibility = "private"
note = "an own-ring tool whose checkout is declared with the profile's placeholder"

[telemetry]
match_command = ["widget"]
since = "2026-09-01"

[[history]]
date = "2026-09-01"
ring = "own"
evidence = "e"
"""

# The other shape an `own` entry takes: published, with no local checkout named at all:
# `repo` is a URL, so there is no worktree to scan and no placeholder to expand.
OWN_REMOTE = """\
name = "remote-widget"
repo = "https://github.com/example/remote-widget"
axis = "python-engineering"
artifact = "cli"
ring = "own"
license = "MIT"
visibility = "public"
note = "an own-ring tool that names no local tree"

[telemetry]
match_command = ["remote-widget"]
since = "2026-09-01"

[[history]]
date = "2026-09-01"
ring = "own"
evidence = "e"
"""


def declare_documents(radar) -> Path:
    """Point the profile's `{documents}` at a directory inside the miniature radar.

    Written here rather than taught to conftest's `write_environment`: the fixture is
    the harness these tests are judged by, and it does not move in the change it judges.
    """
    docs = radar.root / "documents"
    docs.mkdir(exist_ok=True)
    (radar.root / "environments" / "personal.toml").write_text(
        'name = "personal"\n'
        'rings = ["own", "adopt", "pilot"]\n'
        "\n[paths]\n"
        f'claude_home = "{radar.home.as_posix()}"\n'
        f'documents = "{docs.as_posix()}"\n',
        encoding="utf-8",
    )
    return docs


def checkout(path: Path) -> Path:
    """A real git checkout. The scan enumerates a worktree by asking git, so a directory
    that is not one is a different case - and it has its own test below."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["git", "init", "-q"], cwd=path, check=True, capture_output=True, timeout=60
    )
    return path


# --------------------------------------------------------------------- marker helpers
#
# The marker is REWRITTEN by the tests below rather than parameterised in conftest, for
# the same reason `declare_documents` writes the profile here: the fixture is the harness
# this change is judged by, and a harness that moves with the change it judges proves
# nothing. `requires_framework` has to admit MINI_PYPROJECT's 0.2.0 or every command dies
# on the range check before it reaches the check under test.
MARKER = """\
schema = "radar-data/v1"
title = "mini-radar"
default_environment = "personal"
requires_framework = ">=0.2.0,<0.3.0"

[invariant]
needles = [{needles}]
{publish}"""


def write_marker(
    radar,
    *,
    needles: tuple[str, ...] = ("stack-radar",),
    publish: str = "",
    exempt: dict[str, str] | None = None,
) -> None:
    """Rewrite the fixture's `radar.toml` with the needles, `[publish]` and the exemptions
    a test wants.

    A DELIBERATELY UNFAMILIAR NEEDLE is what most of these pass: `stack-radar` is the
    string the pre-change code carried as a literal, so a test that plants that word
    cannot tell a gate that read the marker from a gate that ignored it. The same reasoning
    applies to `exempt`: the key a test declares is never the one the code used to carry.
    """
    body = MARKER.format(
        needles=", ".join(f'"{n}"' for n in needles),
        publish=f"\n[publish]\n{publish}" if publish else "",
    )
    if exempt:
        rows = "\n".join(f'"{k}" = "{v}"' for k, v in exempt.items())
        body += f"\n[invariant.exempt]\n{rows}\n"
    (radar.root / "radar.toml").write_text(body, encoding="utf-8")


def own_entry(name: str, repo: str) -> str:
    """An `own` entry, the shape the engine's own takes: a local checkout in `repo`,
    written with the profile's placeholder, and private while the exception matches on
    that field."""
    return f"""\
name = "{name}"
repo = "{repo}"
axis = "python-engineering"
artifact = "cli"
ring = "own"
license = "MIT"
visibility = "private"
note = "an own-ring entry, for the publication check"

[telemetry]
match_command = ["{name}"]
since = "2026-09-01"

[[history]]
date = "2026-09-01"
ring = "own"
evidence = "e"
"""


SCOPED = """\
name = "inhouse-thing"
repo = "https://example.invalid/inhouse-thing"
axis = "python-engineering"
artifact = "cli"
ring = "adopt"
license = "MIT"
visibility = "private"
note = "a machine-local entry whose NAME must not travel"
redact_as = "inhouse-lib"

[telemetry]
match_command = ["inhouse-thing"]
since = "2026-09-01"

[[history]]
date = "2026-09-01"
ring = "adopt"
evidence = "e"
"""


def scoped_entry(radar) -> None:
    """Install a machine-local entry, and make the fixture root a checkout that ignores
    it - otherwise the scope-leak scan finds the name inside `tools.local/` itself and
    fails for a reason that has nothing to do with the check under test."""
    checkout(radar.root)
    (radar.root / ".gitignore").write_text("tools.local/\n", encoding="utf-8")
    (radar.root / "tools.local").mkdir(exist_ok=True)
    (radar.root / "tools.local" / "inhouse-thing.toml").write_text(SCOPED, encoding="utf-8")


def engine(radar, name: str = "motor-radar") -> Path:
    """A checkout standing in for the engine's, inside the profile's `{documents}`.

    Named `motor-radar` rather than anything generic on purpose: the tests below plant it
    in data-plane files and read FAIL lines back, so the string has to be one no other
    part of the fixture can produce.
    """
    return checkout(declare_documents(radar) / name)


class TestAbsentDataPlane:
    def test_the_skipped_checks_are_named_one_by_one(self, radar):
        radar.tool("ruff", VALID)
        p = radar.gate("--claude-home", ABSENT)
        assert p.returncode == 0, p.stdout + p.stderr
        assert "skipped: the invariant scan" in p.stdout
        assert "scope-leak scan" in p.stdout
        assert "machine probes" in p.stdout
        # The path it looked at is in the message: "no data plane" without saying where
        # it looked is unactionable on the machine where it is wrong.
        assert ABSENT.replace("/", "\\") in p.stdout or ABSENT in p.stdout
        assert "2 check(s) skipped without a data plane" in p.stdout

    def test_a_scan_of_nothing_does_not_claim_the_invariant_holds(self, radar):
        # The exact regression: the old wording was indistinguishable from a real pass.
        radar.tool("ruff", VALID)
        p = radar.gate("--claude-home", ABSENT)
        assert "invariant: holds" not in p.stdout

    def test_a_repo_only_failure_still_fails(self, radar):
        # Skipping the machine half must not soften the half that does run.
        radar.tool("ruff", VALID)
        radar.tool("broken", 'name = "broken"\nring = "adopt"\n')
        p = radar.gate("--claude-home", ABSENT)
        assert p.returncode == 1, p.stdout + p.stderr
        assert "[FAIL] broken:" in p.stdout
        assert "skipped: the invariant scan" in p.stdout  # and it still says what it skipped

    def test_the_version_sites_are_still_compared(self, radar):
        # Version-site equality is a check the ritual runs; it reads two
        # tracked files, so an absent data plane is no excuse for it not to run.
        radar.tool("ruff", VALID)
        (radar.root / "CHANGELOG.md").write_text(
            "# Changelog\n\n## [0.9.9] - 2026-09-03\n\nA heading nothing agrees with.\n",
            encoding="utf-8",
        )
        p = radar.gate("--claude-home", ABSENT)
        assert p.returncode == 1, p.stdout
        assert "[FAIL] version sites:" in p.stdout
        assert "0.9.9" in p.stdout


class TestNoDataPlaneRehearsal:
    """`--no-data-plane` answers as CI does, on a machine where CI's absences are false.

    `--claude-home <nonexistent>` alone does NOT reproduce a runner: the governed tool
    worktrees are still checked out on a developer machine and `tools.local/` is still
    there, so both machine-dependent checks keep running and the rehearsal proves
    nothing about what CI will see.
    """

    def test_it_skips_and_names_even_where_a_data_plane_exists(self, radar):
        radar.tool("ruff", VALID)
        (radar.home / "settings.json").write_text('{"ok": true}\n', encoding="utf-8")
        p = radar.gate("--no-data-plane")
        assert p.returncode == 0, p.stdout + p.stderr
        assert "invariant: holds" not in p.stdout
        assert "--no-data-plane was asked for" in p.stdout
        assert "2 check(s) skipped without a data plane" in p.stdout


class TestPresentDataPlane:
    def test_a_reachable_data_plane_is_scanned_and_says_so(self, radar):
        radar.tool("ruff", VALID)
        (radar.home / "settings.json").write_text('{"ok": true}\n', encoding="utf-8")
        p = radar.gate()
        assert p.returncode == 0, p.stdout + p.stderr
        assert "invariant: holds" in p.stdout
        assert "skipped: the invariant scan" not in p.stdout  # nothing was skipped

    def test_a_data_plane_reference_is_a_failure(self, radar):
        # The positive control: without it, "holds" only proves the scan ran, not that it
        # can bite. A data-plane file naming the control plane is the whole invariant.
        radar.tool("ruff", VALID)
        (radar.home / "CLAUDE.md").write_text(
            "See the stack-radar repo for the ring policy.\n", encoding="utf-8"
        )
        p = radar.gate()
        assert p.returncode == 1, p.stdout + p.stderr
        assert "[FAIL] invariant:" in p.stdout


class TestWorktreeRoots:
    """A governed worktree is declared with a placeholder, so it has to be expanded.

    THE RED PROOF of this section. `data_plane_roots` iterated the entries and tested
    `Path("{documents}/widget").is_dir()`, which is false on every machine, so seven of the
    eight governed worktrees were never a root and the gate printed "holds" over the one
    entry that happened to carry a literal path. Nothing failed, and nothing said so -
    the strongest reading a run could produce was a sentence about a third of the data
    plane (measured on 2026-09-05: 6368 of 18772 eligible files, 1 of 8 worktrees).
    """

    def test_a_worktree_declared_with_a_placeholder_is_scanned(self, radar):
        radar.tool("ruff", VALID)
        radar.tool("widget", OWN)
        wt = checkout(declare_documents(radar) / "widget")
        (wt / "notes.md").write_text(
            "see the stack-radar repo for the ring policy\n", encoding="utf-8"
        )
        p = radar.gate()
        assert p.returncode == 1, p.stdout + p.stderr
        assert "[FAIL] invariant:" in p.stdout
        assert "notes.md" in p.stdout

    def test_a_root_git_cannot_enumerate_fails_instead_of_reporting_zero_of_zero(self, radar):
        # The hazard inherited from `tracked_files()`, which returns an empty list in
        # silence on a non-zero exit. An unenumerable root that contributes no files
        # prints the same "0 scanned / 0 eligible" a clean root does, so the failure to
        # look is indistinguishable from having looked.
        radar.tool("ruff", VALID)
        radar.tool("widget", OWN)
        (declare_documents(radar) / "widget").mkdir()  # a directory, never a checkout
        p = radar.gate()
        assert p.returncode == 1, p.stdout + p.stderr
        assert "could not be enumerated" in p.stdout
        assert "NOT ENUMERABLE" in p.stdout
        assert "0 scanned / 0 eligible" not in p.stdout

    def test_the_note_counts_scanned_and_eligible_under_each_root(self, radar):
        radar.tool("ruff", VALID)
        radar.tool("widget", OWN)
        wt = checkout(declare_documents(radar) / "widget")
        (wt / "a.md").write_text("clean\n", encoding="utf-8")
        (wt / "b.py").write_text("clean\n", encoding="utf-8")
        (wt / "c.bin").write_text("not an eligible suffix\n", encoding="utf-8")
        (radar.home / "settings.json").write_text('{"ok": true}\n', encoding="utf-8")
        p = radar.gate()
        assert p.returncode == 0, p.stdout + p.stderr
        assert "widget: 2 scanned / 2 eligible" in p.stdout
        assert "<claude_home>/settings.json: 1 scanned / 1 eligible" in p.stdout

    def test_an_own_entry_that_never_becomes_a_root_is_named_with_its_reason(self, radar):
        # An absent root has to appear in the note rather than simply not exist, because
        # the roots are what the "holds" sentence is about.
        radar.tool("ruff", VALID)
        radar.tool("widget", OWN)
        radar.tool("remote-widget", OWN_REMOTE)
        checkout(declare_documents(radar) / "widget")
        p = radar.gate()
        assert p.returncode == 0, p.stdout + p.stderr
        assert "not a root: remote-widget" in p.stdout
        assert "remote URL only" in p.stdout
        assert "not a root: widget" not in p.stdout


class TestTheFeedbackExclusion:
    """`~/.claude/feedback` is excluded as ONE ROOT, never as a path component.

    The exclusion comes with a condition stated in the same breath: it is not to be
    written as an INVARIANT_SKIP_DIRS entry: that predicate matches a component at any
    depth, so the word would also delete `widget/docs/feedback/` from the scan - in the
    change whose whole purpose is to reach it.
    """

    def test_a_feedback_directory_inside_a_worktree_is_still_scanned(self, radar):
        radar.tool("ruff", VALID)
        radar.tool("widget", OWN)
        wt = checkout(declare_documents(radar) / "widget")
        (wt / "docs" / "feedback").mkdir(parents=True)
        (wt / "docs" / "feedback" / "report.md").write_text(
            "see the stack-radar repo\n", encoding="utf-8"
        )
        p = radar.gate()
        assert p.returncode == 1, p.stdout + p.stderr
        assert "[FAIL] invariant:" in p.stdout

    def test_the_claude_home_feedback_root_is_out_and_says_why(self, radar):
        radar.tool("ruff", VALID)
        (radar.home / "settings.json").write_text('{"ok": true}\n', encoding="utf-8")
        (radar.home / "feedback").mkdir()
        (radar.home / "feedback" / "report.md").write_text(
            "a mirrored report that names the stack-radar repo\n", encoding="utf-8"
        )
        p = radar.gate()
        assert p.returncode == 0, p.stdout + p.stderr
        assert "<claude_home>/feedback is outside the scan by declaration" in p.stdout


class TestTheCeiling:
    """The 6000-file cap is `--fast`, not the default, and it has to admit when it cut."""

    @staticmethod
    def _many(root: Path, n: int) -> None:
        root.mkdir(parents=True, exist_ok=True)
        for i in range(n):
            (root / f"f{i}.md").write_text("clean\n", encoding="utf-8")

    def test_the_default_scan_has_no_ceiling(self, radar):
        radar.tool("ruff", VALID)
        self._many(radar.home / "skills", 6001)
        p = radar.gate()
        assert p.returncode == 0, p.stdout + p.stderr
        assert "<claude_home>/skills: 6001 scanned / 6001 eligible" in p.stdout
        assert "TRUNCADO" not in p.stdout

    def test_fast_cuts_and_says_truncado(self, radar):
        radar.tool("ruff", VALID)
        self._many(radar.home / "skills", 6001)
        p = radar.gate("--fast")
        assert p.returncode == 0, p.stdout + p.stderr
        assert "<claude_home>/skills: 6000 scanned / 6001 eligible" in p.stdout
        assert "TRUNCADO" in p.stdout


class TestTheDeclaredClaudeHomeFiles:
    """The sub-path list stopped being closed by omission: it names what arrives later."""

    def test_a_declared_file_that_does_not_exist_yet_is_skipped_in_silence(self, radar):
        radar.tool("ruff", VALID)
        (radar.home / "settings.json").write_text('{"ok": true}\n', encoding="utf-8")
        p = radar.gate()
        assert p.returncode == 0, p.stdout + p.stderr
        assert "[WARN]" not in p.stdout
        assert "dispatch-overlay.toml" not in p.stdout

    def test_the_later_arrivals_are_scanned_the_day_they_land(self, radar):
        # dispatch-overlay.toml is written by this repository itself, so a renderer that
        # leaks the control plane's identity is only caught if the scan reads what it
        # wrote. model-mirrors.toml is the one data-plane file no CI anywhere reaches.
        radar.tool("ruff", VALID)
        for name in ("dispatch-overlay.toml", "dispatch-table.json", "model-mirrors.toml"):
            (radar.home / name).write_text(
                'note = "written from the stack-radar tree"\n', encoding="utf-8"
            )
            p = radar.gate()
            assert p.returncode == 1, f"{name}: {p.stdout}{p.stderr}"
            assert name in p.stdout
            (radar.home / name).unlink()


class TestTheNeedleComesFromTheMarker:
    """`INVARIANT_NEEDLES` stops being a literal in the code.

    THE RED PROOF of the first half of that change. The pre-change gate carried
    `INVARIANT_NEEDLES = ("stack-radar",)`, so a catalogue that declared a different
    needle was scanned for a word it had not asked for and not scanned for the word it
    had. Two failure modes, both silent and both printing "holds": a renamed bank whose
    real name is never searched for, and - after the engine ships
    under `stack-radar` - a FAIL on every legitimate mention of a published
    tool, which teaches the operator to skip the line.

    So the tests plant a needle the pre-change literal never carried, and the converse:
    the old literal must no longer bite on a catalogue that does not declare it.
    """

    def test_a_needle_declared_in_the_marker_bites(self, radar):
        radar.tool("ruff", VALID)
        write_marker(radar, needles=("banco-privado",))
        (radar.home / "CLAUDE.md").write_text(
            "See the banco-privado repo for the ring policy.\n", encoding="utf-8"
        )
        p = radar.gate()
        assert p.returncode == 1, p.stdout + p.stderr
        assert "[FAIL] invariant:" in p.stdout
        assert "banco-privado" in p.stdout

    def test_the_needle_the_code_used_to_carry_is_no_longer_one(self, radar):
        # The other half, and the one only a marker-driven gate can pass: a catalogue
        # that does not declare `stack-radar` must not be scanned for it.
        radar.tool("ruff", VALID)
        write_marker(radar, needles=("banco-privado",))
        (radar.home / "CLAUDE.md").write_text(
            "stack-radar is a published tool; naming it here is not a binding.\n",
            encoding="utf-8",
        )
        p = radar.gate()
        assert p.returncode == 0, p.stdout + p.stderr
        assert "[FAIL] invariant:" not in p.stdout

    def test_the_absolute_path_is_still_added_to_the_declared_needles(self, radar):
        # The gate already appended the data root's path; the section keeps it. A needle
        # list is a list of NAMES, and a name alone is the weak half - radar.toml's own
        # comment says why: a generic one fires on ordinary prose, a stale one fires
        # nowhere, and both print the same word.
        radar.tool("ruff", VALID)
        write_marker(radar, needles=("banco-privado",))
        (radar.home / "CLAUDE.md").write_text(
            f"cd {radar.root.as_posix()} && radar gate\n", encoding="utf-8"
        )
        p = radar.gate()
        assert p.returncode == 1, p.stdout + p.stderr
        assert "[FAIL] invariant:" in p.stdout

    def test_an_exemption_declared_in_the_marker_spares_the_file_and_is_printed(self, radar):
        # The exemption list used to be a constant in the gate module, and a constant was
        # the wrong home for the same reason the needles were - plus one the needles did
        # not have: the key spells a path inside a repository that is not this one, so a
        # published engine carried a private sibling's name in its own source.
        radar.tool("ruff", VALID)
        radar.tool("widget", OWN)
        wt = checkout(declare_documents(radar) / "widget")
        (wt / "ablation.py").write_text('forbidden = ("banco-privado",)\n', encoding="utf-8")
        write_marker(
            radar,
            needles=("banco-privado",),
            exempt={"widget/ablation.py": "a negative assertion, not a dependency"},
        )
        p = radar.gate()
        assert p.returncode == 0, p.stdout + p.stderr
        assert "[FAIL] invariant:" not in p.stdout
        assert "invariant exemption in effect - widget/ablation.py" in p.stdout
        assert "a negative assertion, not a dependency" in p.stdout

    def test_the_same_file_fails_when_the_marker_declares_no_exemption(self, radar):
        # The converse, and the half that proves the exemption is READ rather than
        # inherited: the identical file, with the key absent, must bite.
        radar.tool("ruff", VALID)
        radar.tool("widget", OWN)
        wt = checkout(declare_documents(radar) / "widget")
        (wt / "ablation.py").write_text('forbidden = ("banco-privado",)\n', encoding="utf-8")
        write_marker(radar, needles=("banco-privado",))
        p = radar.gate()
        assert p.returncode == 1, p.stdout + p.stderr
        assert "[FAIL] invariant:" in p.stdout
        assert "invariant exemption in effect" not in p.stdout

    def test_an_exemption_does_not_spare_a_file_its_key_does_not_name(self, radar):
        # THE CASE THE FIRST TWO MISS, and a one-line mutation proves it: replacing the
        # `endswith` lookup with `next(iter(exempt_map), None)` — exempt ANY file as soon as
        # any exemption is declared — leaves both of them green, and the whole suite with
        # them. With `[invariant.exempt]` declared, as the real catalogue declares it, that
        # mutation switches the invariant scan off entirely while still printing
        # "holds ... 1 exemption(s)": the same sentence a real pass produces, which is the
        # decay the module header exists to prevent. So a second file has to bite while the
        # first is spared, in the same run.
        radar.tool("ruff", VALID)
        radar.tool("widget", OWN)
        wt = checkout(declare_documents(radar) / "widget")
        (wt / "ablation.py").write_text('forbidden = ("banco-privado",)\n', encoding="utf-8")
        (wt / "notes.md").write_text("see the banco-privado repo\n", encoding="utf-8")
        write_marker(
            radar,
            needles=("banco-privado",),
            exempt={"widget/ablation.py": "a negative assertion, not a dependency"},
        )
        p = radar.gate()
        assert p.returncode == 1, p.stdout + p.stderr
        assert "[FAIL] invariant:" in p.stdout
        assert "notes.md" in p.stdout
        # ...and the spared file is still spared, in the same run rather than another.
        assert "invariant exemption in effect - widget/ablation.py" in p.stdout
        assert "ablation.py" not in p.stdout.split("[FAIL] invariant")[1]

    def test_the_key_is_a_suffix_rather_than_a_substring(self, radar):
        # "A key is an `endswith` suffix" is repeated in three docstrings and in the
        # catalogue's own comment, and nothing proved it: swapping `posix.endswith(k)` for
        # `k in posix` also survives every other test here. A key that matches mid-path
        # would spare files nobody declared.
        radar.tool("ruff", VALID)
        radar.tool("widget", OWN)
        wt = checkout(declare_documents(radar) / "widget")
        (wt / "ablation.py").write_text('forbidden = ("banco-privado",)\n', encoding="utf-8")
        write_marker(
            radar,
            needles=("banco-privado",),
            # A substring of the path, but not a suffix of it: `widget/abl` sits inside
            # `.../widget/ablation.py` and must NOT exempt it.
            exempt={"widget/abl": "a prefix, which is not what a suffix key means"},
        )
        p = radar.gate()
        assert p.returncode == 1, p.stdout + p.stderr
        assert "[FAIL] invariant:" in p.stdout
        assert "invariant exemption in effect" not in p.stdout

    def test_a_malformed_exemption_is_ignored_and_says_so(self, radar):
        # The typo this catches parses as valid TOML: writing `[invariant.exempt.<key>]`
        # instead of a row INSIDE `[invariant.exempt]` gives the reader a nested table. The
        # first version of this reader turned that into a LIVE exemption keyed by the table
        # name, whose printed "reason" was the repr of a dict - so the one accountability
        # mechanism the design has became noise, and a file nobody meant to exempt was.
        #
        # The rule: a declaration the code drops must not look like a declaration nobody
        # wrote. It is dropped AND named, and the scan stays stricter rather than looser.
        radar.tool("ruff", VALID)
        radar.tool("widget", OWN)
        wt = checkout(declare_documents(radar) / "widget")
        (wt / "ablation.py").write_text('forbidden = ("banco-privado",)\n', encoding="utf-8")
        body = (radar.root / "radar.toml").read_text(encoding="utf-8")
        (radar.root / "radar.toml").write_text(
            body.replace('needles = ["stack-radar"]', 'needles = ["banco-privado"]')
            + '\n[invariant.exempt."widget/ablation.py"]\nreason = "written one level too deep"\n',
            encoding="utf-8",
        )
        p = radar.gate()
        assert p.returncode == 1, p.stdout + p.stderr
        assert "[FAIL] invariant:" in p.stdout
        assert "IGNORED" in p.stdout
        assert "invariant exemption in effect" not in p.stdout

    def test_a_marker_that_declares_no_needle_says_the_scan_is_weaker(self, radar):
        # The failure radar.toml's own comment names: with no name needle the scan still
        # prints "holds", because the path needle alone carries the sentence. An
        # undeclared needle is reported rather than inferred.
        radar.tool("ruff", VALID)
        write_marker(radar, needles=())
        (radar.home / "settings.json").write_text('{"ok": true}\n', encoding="utf-8")
        p = radar.gate()
        assert p.returncode == 0, p.stdout + p.stderr
        assert "[invariant].needles" in p.stdout
        assert "only the data root's absolute path" in p.stdout


class TestTheEngineNameInExecutionFiles:
    """The engine's name is free in the data plane, three files apart.

    `settings.json`, any `hooks/hooks.json` and `CLAUDE.md` are the files that make a
    session RUN something, and they are what keeps the rule - "the
    radar is never an execution dependency" - checkable once the word `stack-radar`
    names a published tool rather than this repository. Mentioning the engine in a note
    is ordinary; wiring a hook to it is not.

    The engine is identified the way the gate identifies it everywhere else: the `own` entry
    whose `repo` resolves to `[publish].framework_worktree`. No second key declares the
    name, so the two readings cannot drift apart.
    """

    def _setup(self, radar) -> None:
        radar.tool("ruff", VALID)
        radar.tool("motor-radar", own_entry("motor-radar", "{documents}/motor-radar"))
        write_marker(
            radar,
            needles=("banco-privado",),
            publish='framework_worktree = "{documents}/motor-radar"\n',
        )
        engine(radar)

    def test_the_name_in_claude_md_is_a_failure(self, radar):
        self._setup(radar)
        (radar.home / "CLAUDE.md").write_text(
            "Always run motor-radar before committing.\n", encoding="utf-8"
        )
        p = radar.gate()
        assert p.returncode == 1, p.stdout + p.stderr
        assert "[FAIL] invariant:" in p.stdout
        assert "CLAUDE.md" in p.stdout

    def test_the_name_in_settings_json_is_a_failure(self, radar):
        self._setup(radar)
        (radar.home / "settings.json").write_text(
            '{"hooks": {"Stop": "motor-radar gate"}}\n', encoding="utf-8"
        )
        p = radar.gate()
        assert p.returncode == 1, p.stdout + p.stderr
        assert "[FAIL] invariant:" in p.stdout
        assert "settings.json" in p.stdout

    def test_the_name_in_any_plugin_hooks_file_is_a_failure(self, radar):
        self._setup(radar)
        hooks = radar.home / "plugins" / "some-plugin" / "hooks"
        hooks.mkdir(parents=True)
        (hooks / "hooks.json").write_text(
            '{"PostToolUse": [{"command": "motor-radar gate"}]}\n', encoding="utf-8"
        )
        p = radar.gate()
        assert p.returncode == 1, p.stdout + p.stderr
        assert "[FAIL] invariant:" in p.stdout
        assert "hooks.json" in p.stdout

    def test_the_name_anywhere_else_in_the_data_plane_is_free(self, radar):
        # The positive control the item turns on. If naming the engine failed everywhere,
        # the scan would be red the day somebody installs it - the outcome the needle
        # rules exist to avoid, because a needle that fires on a legitimate mention teaches
        # the operator to skip the FAIL line.
        self._setup(radar)
        (radar.home / "skills").mkdir()
        (radar.home / "skills" / "note.md").write_text(
            "motor-radar is one of the tools in the stack.\n", encoding="utf-8"
        )
        p = radar.gate()
        assert p.returncode == 0, p.stdout + p.stderr
        assert "[FAIL] invariant:" not in p.stdout

    def test_it_says_when_the_engine_has_no_name_yet(self, radar):
        # Until somebody writes the engine's `own` entry there is nothing to identify it BY,
        # and a check that quietly stops looking is the decay this repository keeps naming.
        radar.tool("ruff", VALID)
        write_marker(radar, needles=("banco-privado",))
        (radar.home / "settings.json").write_text('{"ok": true}\n', encoding="utf-8")
        p = radar.gate()
        assert p.returncode == 0, p.stdout + p.stderr
        assert "the engine's name is NOT a needle" in p.stdout
        assert "framework_worktree" in p.stdout


class TestThePublicationCheck:
    """`check_publishable`, the one check only the private repository can run.

    The list of what must not travel - the bank's name and path, the
    machine-local tool names, the derived list of every `own` entry - exists only here,
    so the engine's own CI checks FORMS and never names. The check reads `git ls-files`
    of the declared engine checkout and fails on any of them.

    THE SECOND RED PROOF of that change is `test_the_name_of_an_own_entry_fails`: a
    temporary "engine checkout" carrying the name of an `own` entry. Before it there was
    no such check at all, so the leak would ship with the first release.
    """

    def test_it_does_not_run_and_does_not_pass_when_the_field_is_absent(self, radar):
        # A catalogue written before the engine had a repository of its own has no
        # checkout to declare, so the key can be absent. A check that cannot run has to
        # say so: "no findings" out of a check that read nothing is the wording of a pass.
        radar.tool("ruff", VALID)
        write_marker(radar, needles=("banco-privado",))
        p = radar.gate()
        assert p.returncode == 0, p.stdout + p.stderr
        assert "publication check did NOT run" in p.stdout
        assert "[publish].framework_worktree" in p.stdout

    def test_the_name_of_an_own_entry_fails(self, radar):
        radar.tool("ruff", VALID)
        radar.tool("widget", OWN)
        write_marker(
            radar,
            needles=("banco-privado",),
            publish='framework_worktree = "{documents}/motor-radar"\n',
        )
        wt = engine(radar)
        (wt / "README.md").write_text("Built alongside widget.\n", encoding="utf-8")
        p = radar.gate()
        assert p.returncode == 1, p.stdout + p.stderr
        assert "[FAIL] publication check" in p.stdout
        assert "widget" in p.stdout
        assert "README.md" in p.stdout

    def test_the_engine_s_own_entry_is_excepted_by_identity(self, radar):
        # The exception by identity. The engine is an
        # `own` entry named after itself, so without this the check is red forever on the
        # engine's own pyproject and banners. It matches on `repo` AFTER expanding
        # `{documents}` and resolving both sides - a literal string compare never does.
        radar.tool("ruff", VALID)
        radar.tool("motor-radar", own_entry("motor-radar", "{documents}/motor-radar"))
        write_marker(
            radar,
            needles=("banco-privado",),
            publish='framework_worktree = "{documents}/motor-radar"\n',
        )
        wt = engine(radar)
        (wt / "pyproject.toml").write_text('[project]\nname = "motor-radar"\n', encoding="utf-8")
        p = radar.gate()
        assert p.returncode == 0, p.stdout + p.stderr
        assert "[FAIL] publication check" not in p.stdout
        assert "is the engine's own entry" in p.stdout

    def test_a_sibling_own_entry_still_fails_beside_the_exception(self, radar):
        # The exception is for ONE entry, identified by its checkout. Every other `own`
        # name stays forbidden in the same run.
        radar.tool("ruff", VALID)
        radar.tool("motor-radar", own_entry("motor-radar", "{documents}/motor-radar"))
        radar.tool("widget", OWN)
        write_marker(
            radar,
            needles=("banco-privado",),
            publish='framework_worktree = "{documents}/motor-radar"\n',
        )
        wt = engine(radar)
        (wt / "pyproject.toml").write_text('[project]\nname = "motor-radar"\n', encoding="utf-8")
        (wt / "docs.md").write_text("widget is in the same estate.\n", encoding="utf-8")
        p = radar.gate()
        assert p.returncode == 1, p.stdout + p.stderr
        assert "widget" in p.stdout

    def test_the_bank_s_name_and_path_fail(self, radar):
        radar.tool("ruff", VALID)
        write_marker(
            radar,
            needles=("banco-privado",),
            publish='framework_worktree = "{documents}/motor-radar"\n',
        )
        wt = engine(radar)
        (wt / "a.md").write_text("banco-privado holds the catalogue.\n", encoding="utf-8")
        (wt / "b.md").write_text(f"cd {radar.root.as_posix()}\n", encoding="utf-8")
        p = radar.gate()
        assert p.returncode == 1, p.stdout + p.stderr
        assert "a.md" in p.stdout
        assert "b.md" in p.stdout

    def test_a_machine_local_tool_name_fails(self, radar):
        radar.tool("ruff", VALID)
        scoped_entry(radar)
        write_marker(
            radar,
            needles=("banco-privado",),
            publish='framework_worktree = "{documents}/motor-radar"\n',
        )
        wt = engine(radar)
        (wt / "note.md").write_text("ported from inhouse-thing\n", encoding="utf-8")
        p = radar.gate()
        assert p.returncode == 1, p.stdout + p.stderr
        assert "[FAIL] publication check" in p.stdout
        assert "inhouse-thing" in p.stdout

    def test_one_string_forbidden_twice_is_one_finding(self, radar):
        # A machine-local entry at ring `own` is reached by two branches of the derived
        # list - it is a scoped name AND an `own` name - and the same leak reported twice
        # inflates the count the check ends on.
        radar.tool("ruff", VALID)
        scoped_entry(radar)
        (radar.root / "tools.local" / "inhouse-thing.toml").write_text(
            SCOPED.replace('ring = "adopt"', 'ring = "own"'), encoding="utf-8"
        )
        write_marker(
            radar,
            needles=("banco-privado",),
            publish='framework_worktree = "{documents}/motor-radar"\n',
        )
        wt = engine(radar)
        (wt / "note.md").write_text("ported from inhouse-thing\n", encoding="utf-8")
        p = radar.gate()
        assert p.returncode == 1, p.stdout + p.stderr
        findings = [ln for ln in p.stdout.splitlines() if ln.startswith("[FAIL] publication check")]
        assert len(findings) == 1, findings
        assert "machine-local tool" in findings[0]

    def test_a_forbid_extra_term_fails(self, radar):
        # The operator's own list: the operating-system user name, a private remote. A
        # key rather than a constant because only this machine knows the strings.
        radar.tool("ruff", VALID)
        write_marker(
            radar,
            needles=("banco-privado",),
            publish=(
                'framework_worktree = "{documents}/motor-radar"\nforbid_extra = ["acme-corp"]\n'
            ),
        )
        wt = engine(radar)
        (wt / "note.md").write_text("internal at acme-corp\n", encoding="utf-8")
        p = radar.gate()
        assert p.returncode == 1, p.stdout + p.stderr
        assert "forbid_extra" in p.stdout

    def test_a_forbidden_term_in_a_filename_fails(self, radar):
        # The lesson check_scope_leak learned the hard way: eighteen reports carried the
        # name in their own filename, which leaks from a directory listing unopened.
        radar.tool("ruff", VALID)
        radar.tool("widget", OWN)
        write_marker(
            radar,
            needles=("banco-privado",),
            publish='framework_worktree = "{documents}/motor-radar"\n',
        )
        wt = engine(radar)
        (wt / "widget-port.md").write_text("nothing in the body.\n", encoding="utf-8")
        p = radar.gate()
        assert p.returncode == 1, p.stdout + p.stderr
        assert "FILENAME" in p.stdout
        assert "widget-port.md" in p.stdout

    def test_path_home_fails_outside_bootstrap_and_passes_inside_it(self, radar):
        radar.tool("ruff", VALID)
        write_marker(
            radar,
            needles=("banco-privado",),
            publish='framework_worktree = "{documents}/motor-radar"\n',
        )
        wt = engine(radar)
        (wt / "bootstrap.py").write_text("home = Path.home()\n", encoding="utf-8")
        clean = radar.gate()
        assert clean.returncode == 0, clean.stdout + clean.stderr
        (wt / "versions.py").write_text("home = Path.home()\n", encoding="utf-8")
        p = radar.gate()
        assert p.returncode == 1, p.stdout + p.stderr
        assert "Path.home()" in p.stdout
        assert "versions.py calls `Path.home()`" in p.stdout
        # The exempt module is named in the guidance the FAIL prints, so the assertion
        # has to be about the SUBJECT of a finding rather than about the word appearing.
        assert "bootstrap.py calls `Path.home()`" not in p.stdout

    def test_a_real_user_path_fails_and_a_placeholder_does_not(self, radar):
        radar.tool("ruff", VALID)
        write_marker(
            radar,
            needles=("banco-privado",),
            publish='framework_worktree = "{documents}/motor-radar"\n',
        )
        wt = engine(radar)
        (wt / "docs.md").write_text(
            "Put it under C:/Users/{user}/Documents, or C:/Users/<you>/Documents.\n",
            encoding="utf-8",
        )
        clean = radar.gate()
        assert clean.returncode == 0, clean.stdout + clean.stderr
        (wt / "leak.md").write_text("Mine lives at C:/Users/someone/Documents\n", encoding="utf-8")
        p = radar.gate()
        assert p.returncode == 1, p.stdout + p.stderr
        assert "leak.md" in p.stdout
        assert "docs.md" not in p.stdout

    def test_a_checkout_git_cannot_enumerate_is_a_failure(self, radar):
        # The same rule the invariant roots follow: a root that could not be read prints
        # the wording of a clean one unless the failure to look is itself reported.
        radar.tool("ruff", VALID)
        write_marker(
            radar,
            needles=("banco-privado",),
            publish='framework_worktree = "{documents}/motor-radar"\n',
        )
        (declare_documents(radar) / "motor-radar").mkdir()  # a directory, never a checkout
        p = radar.gate()
        assert p.returncode == 1, p.stdout + p.stderr
        assert "[FAIL] publication check" in p.stdout
        assert "could not be enumerated" in p.stdout

    def test_a_declared_checkout_that_is_absent_is_named_rather_than_passed(self, radar):
        # CI has no engine checkout, and the key lives in the tracked marker. Absence is
        # a named non-run, never a FAIL and never a pass.
        radar.tool("ruff", VALID)
        write_marker(
            radar,
            needles=("banco-privado",),
            publish='framework_worktree = "{documents}/motor-radar"\n',
        )
        declare_documents(radar)
        p = radar.gate()
        assert p.returncode == 0, p.stdout + p.stderr
        assert "publication check did NOT run" in p.stdout
        assert "motor-radar" in p.stdout

    def test_the_rehearsal_does_not_pretend_to_have_run_it(self, radar):
        radar.tool("ruff", VALID)
        write_marker(
            radar,
            needles=("banco-privado",),
            publish='framework_worktree = "{documents}/motor-radar"\n',
        )
        engine(radar)
        p = radar.gate("--no-data-plane")
        assert p.returncode == 0, p.stdout + p.stderr
        assert "publication check did NOT run" in p.stdout
        assert "--no-data-plane" in p.stdout


# A public entry pointing at a local checkout, never a URL.
PUBLIC_LOCAL_REPO = """\
name = "local-tool"
repo = "C:/Users/example/local-tool"
axis = "python-engineering"
artifact = "cli"
ring = "adopt"
license = "MIT"
visibility = "public"
note = "a public entry whose repo is a local path, not a URL"

[telemetry]
match_command = ["local-tool"]
since = "2026-09-01"

[[history]]
date = "2026-09-01"
ring = "adopt"
evidence = "e"
"""

# The admitted exception: a phantom tool with no repository at all.
PUBLIC_SENTINEL_REPO = """\
name = "phantom-tool"
repo = "(repo does not exist)"
axis = "orchestration"
artifact = "unknown"
ring = "discard"
license = "n/a"
visibility = "public"
note = "a phantom entry, admitted by the sentinel"

[[history]]
date = "2026-09-01"
ring = "discard"
evidence = "e"
"""


class TestPublicVisibilityNeedsAURLOrTheSentinel:
    """render.py writes a public entry's `repo` as a Markdown link, so a public
    entry naming a local checkout would publish that path. The one admitted exception
    is the literal `(repo does not exist)` sentinel, for a tool that never had a
    repository."""

    def test_a_public_entry_with_a_local_repo_fails(self, radar):
        radar.tool("ruff", VALID)
        radar.tool("local-tool", PUBLIC_LOCAL_REPO)
        p = radar.gate()
        assert p.returncode == 1, p.stdout + p.stderr
        assert "[FAIL] local-tool:" in p.stdout
        assert "visibility=public" in p.stdout

    def test_a_public_entry_with_the_sentinel_passes(self, radar):
        radar.tool("ruff", VALID)
        radar.tool("phantom-tool", PUBLIC_SENTINEL_REPO)
        p = radar.gate()
        assert p.returncode == 0, p.stdout + p.stderr
        assert "phantom-tool" not in p.stdout
