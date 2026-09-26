"""The version sites agree, by a test that fails rather than by convention.

The release layer failed four independent ways in the estate on one day, and the
pattern behind all four was the same: the rule existed in prose and the mechanism did
not. The remedy is equality of version sites by a FAILING test - never by convention,
and never by an assertion of mere presence.

THREE SITES, and the third arrived with distribution. While the engine ran in place as a
flat module directory there were two - `[project].version` and the newest RELEASED
heading of CHANGELOG.md - and the shortness of that list was a property of the thing:
nothing was published, so no namespace carried a version of its own. A package that ships
does carry one, `stack_radar.__version__`, and it is the site an INSTALLED copy answers
from: `radar --version` reads it, and so does anything that imports the package rather
than reading its metadata. A third site that nothing compares is the same defect the
other two were given a test for.

The third site is OPTIONAL, and that is what lets one implementation serve two kinds of
repository. `version_site_errors` is called against a data root as well - a catalogue has
a `pyproject.toml` and a `CHANGELOG.md` and no package at all - so a missing third site
is a two-site repository rather than a failure. The package is located from
`[project].name`, which is the key that already says what is built; a name that stops
matching the directory is caught here rather than at install time.

`uv.lock` is a fourth place the string appears and is deliberately not asserted: `uv run`
re-locks before pytest can read it, so an assertion here could never go red, which is
worse than none. `uv lock --check` is that file's mechanism, and CI runs it first.

`RADAR_ENGINE_SRC` selects which tree is read. It exists for the red proof: point it at a
`src/` extracted from an earlier commit and the same tests run against the pre-change
files. It is the same variable the rest of the suite uses to choose an engine.
"""

from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path

from stack_radar.radar_lib import release_headings, version_site_errors

REPO = Path(__file__).resolve().parent.parent
SRC = Path(os.environ.get("RADAR_ENGINE_SRC") or (REPO / "src"))
CHANGELOG = REPO / "CHANGELOG.md"
PYPROJECT = REPO / "pyproject.toml"

_DUNDER = re.compile(r"^__version__\s*=\s*[\"']([^\"']+)[\"']", re.M)


def headings() -> list[tuple[str, str]]:
    return release_headings(REPO)


def package_init() -> Path:
    name = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["name"]
    return SRC / name.replace("-", "_") / "__init__.py"


def test_the_changelog_exists_and_carries_a_released_heading():
    # Without this the equality test below would pass vacuously on an empty file: no
    # headings, nothing to compare, green. The estate's fourth failure of 2026-08-28 was
    # exactly a repo whose release doctrine was written and never exercised.
    assert CHANGELOG.is_file(), f"no CHANGELOG.md at {CHANGELOG}"
    assert headings(), (
        "CHANGELOG.md carries no `## [X.Y.Z] - YYYY-MM-DD` heading - the version site "
        "this test compares against does not exist"
    )


def test_the_package_version_equals_the_newest_released_heading():
    declared = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["version"]
    newest, date = headings()[0]
    assert declared == newest, (
        f"pyproject.toml says {declared}, CHANGELOG.md's newest released heading says "
        f"{newest} (dated {date}). A release rolls both in the same metadata-only commit."
    )


def test_the_third_site_exists_and_is_read_as_text():
    # Non-vacuity for the test below, and it is not the same assertion twice: a package
    # whose `__init__` stopped declaring `__version__` would leave the comparison with
    # nothing to disagree with, and `version_site_errors` treats an absent third site as a
    # legitimate two-site repository. Here - in the engine's own suite, where the site is
    # not optional - its absence has to be the failure it would be.
    init = package_init()
    assert init.is_file(), (
        f"no {init} - `[project].name` names a package that is not there, so the third "
        "version site cannot be read and the check silently drops to two"
    )
    assert _DUNDER.search(init.read_text(encoding="utf-8")), (
        f"{init} declares no `__version__` - a distributed package carries its version in "
        "its own namespace, and this is the site an installed copy answers from"
    )


def test_the_dunder_version_equals_the_other_two():
    declared = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["version"]
    found = _DUNDER.search(package_init().read_text(encoding="utf-8"))
    assert found and found.group(1) == declared, (
        f"pyproject.toml says {declared}, {package_init().name} says "
        f"{found.group(1) if found else '(nothing)'}. They are the same fact, and a "
        "release rolls all three sites in one metadata-only commit."
    )


def test_the_release_headings_descend():
    # A heading that does not move the version forward is how a version comes to name two
    # different trees. Compared as tuples, so 0.10.0 sorts after 0.9.0.
    versions = [tuple(int(p) for p in v.split(".")) for v, _ in headings()]
    assert versions == sorted(versions, reverse=True), (
        f"the release headings are not in descending version order: {versions}"
    )


def _repo(root: Path, *, pyproject: str, changelog: str, dunder: str | None) -> Path:
    """A synthetic repository with two version sites, or three when `dunder` is given."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "mini-engine"\nversion = "{pyproject}"\n', encoding="utf-8"
    )
    (root / "CHANGELOG.md").write_text(
        f"# Changelog\n\n## [{changelog}] - 2026-09-13\n\nthe fixture's own release.\n",
        encoding="utf-8",
    )
    if dunder is not None:
        pkg = root / "src" / "mini_engine"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text(f'__version__ = "{dunder}"\n', encoding="utf-8")
    return root


class TestTheThirdSiteIsActuallyCompared:
    """The shared implementation reads three sites, proven by making them disagree.

    The tests above read the three files themselves, so they would all stay green if
    `version_site_errors` quietly kept comparing two - which is the exact shape of the
    defect this whole module exists to prevent, one fact with two implementations. These
    plant the disagreement in a synthetic repository instead, where the only thing that
    can notice it is the implementation.
    """

    def test_a_third_site_that_disagrees_is_reported(self, tmp_path: Path):
        root = _repo(tmp_path / "r", pyproject="0.1.0", changelog="0.1.0", dunder="0.2.0")
        errs = version_site_errors(root)
        assert errs, "the package's __version__ disagrees with the other two and nothing said so"
        assert "0.2.0" in " ".join(errs), errs

    def test_three_sites_that_agree_are_silent(self, tmp_path: Path):
        root = _repo(tmp_path / "r", pyproject="0.1.0", changelog="0.1.0", dunder="0.1.0")
        assert version_site_errors(root) == []

    def test_a_repository_with_no_package_still_has_two_sites(self, tmp_path: Path):
        # The optional arm, and it carries its weight: the same verb is run against a data
        # root, which has a pyproject and a changelog and no package at all. Treating the
        # absent site as a failure would make the check unusable there; treating it as
        # agreement would be the silence this module refuses everywhere else - so it is
        # reported as neither, and the two sites that DO exist are still compared.
        root = _repo(tmp_path / "r", pyproject="0.1.0", changelog="0.1.0", dunder=None)
        assert version_site_errors(root) == []

    def test_a_missing_package_does_not_mask_the_other_two(self, tmp_path: Path):
        root = _repo(tmp_path / "r", pyproject="0.1.0", changelog="0.2.0", dunder=None)
        assert version_site_errors(root), "a two-site repository stopped comparing its two"


def test_the_gate_reads_the_same_rule():
    # The gate asks the same question during the ritual, and two implementations of one
    # fact drift silently - the defect this repo built a whole --reconcile flag for. So
    # both call radar_lib.version_site_errors, and this pins that they still agree.
    #
    # It is also the only test here that exercises the THIRD site through the shared
    # implementation rather than re-reading the file itself: the three above are the
    # oracle, and this is the code the gate and the `check-version-sites` verb run.
    assert version_site_errors(REPO) == []
