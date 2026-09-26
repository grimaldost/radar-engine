"""Create a catalogue, and write entries into one: `radar init` and `radar add`.

THE ONLY TWO VERBS THAT WRITE A CATALOGUE RATHER THAN READ ONE, which is why they share a
module. Everything else in this package starts by resolving a data root and then reports on
what it found; `init` creates the root that the rest of the engine needs in order to run at
all, and `add` writes the first thing into it. Before they existed a data root could only be
made by hand, or by a seeding script written against one particular tree - so "install the
engine and start a radar" was a documented sequence of file creations rather than a command,
and every catalogue in existence was shaped by whoever last copied one.

`init` IS THE ONE VERB THAT DOES NOT RESOLVE A ROOT. It cannot: it is handed the directory
that is about to become one, and a resolver that searched upwards from there would find
whatever catalogue happens to sit above it and write into that instead. It is also why this
module asks `bootstrap` for the home directory rather than reaching for the machine itself -
the discovery of where a machine keeps its things is that module's job, it is exempted from
the publication check by name for exactly that reason, and two modules that both decided it
would be two answers to one question.

WHAT IS A TEMPLATE AND WHAT IS COMPUTED, because the line is a rule rather than a
convenience. Prose, comments and structure are templates: they are the same for every
catalogue, and a generated file whose comments explain nothing is a file the first reader
deletes. Paths are COMPUTED - the environment profile carries what was found on this
machine, never a placeholder for the operator to fill in - because a template path is a
guess, and a guess in a profile fails later and somewhere else than where it was made.
"""

from __future__ import annotations

import argparse
import datetime
import sys
import tomllib
from importlib import resources
from pathlib import Path

from .bootstrap import discovered_paths, home_dir, starter_profile
from .paths import MARKER, tools_dir
from .radar_lib import (
    ARTIFACTS,
    AXES,
    RINGS,
    begin_command,
    framework_errors,
    missing_pilot_exit,
    missing_telemetry,
    validate,
)
from .render import DEFAULT_BOUNDARY_NOTE

_PACKAGE = __package__ or "stack_radar"
TEMPLATES = "templates"

# The version a catalogue is born at, and the date grammar its changelog heading uses.
INITIAL_VERSION = "0.1.0"

# The environment a fresh catalogue has. One profile, named after nothing in particular:
# a machine's role is the operator's to describe, and "default" is the one name that does
# not pretend to know it.
DEFAULT_ENVIRONMENT = "default"

# TEMPLATE -> PATH IN THE NEW CATALOGUE, and the indirection is not decoration. Three of
# these are dotfiles, and a template called `.gitignore` inside this package would be read
# by git as a real ignore file for the package directory - so the stored names carry no
# leading dot and a `.tmpl` suffix, which also keeps a linter or a build backend from
# mistaking a template for the config file it is a template OF.
LAYOUT: tuple[tuple[str, str], ...] = (
    ("radar.toml.tmpl", MARKER),
    ("pyproject.toml.tmpl", "pyproject.toml"),
    ("CHANGELOG.md.tmpl", "CHANGELOG.md"),
    ("gitignore.tmpl", ".gitignore"),
    ("pre-commit-config.yaml.tmpl", ".pre-commit-config.yaml"),
    ("ci.yml.tmpl", ".github/workflows/ci.yml"),
    ("githooks-pre-commit.tmpl", ".githooks/pre-commit"),
    ("githooks-commit-msg.tmpl", ".githooks/commit-msg"),
    ("tools-README.md.tmpl", "tools/README.md"),
)

# Git skips a hook without the executable bit on POSIX and says nothing, so a catalogue
# created there would report its lane armed while nothing fired. Set here; on Windows the
# bit does not exist in the filesystem and the hook template says what to run instead.
EXECUTABLE = (".githooks/pre-commit", ".githooks/commit-msg")

# Created even when nothing is written into them yet. `tools/` carries a README so the
# directory survives a commit (git tracks files, not directories) and so the first reader
# finds the entry schema where the entries go.
DIRECTORIES = ("tools", "environments", ".githooks", ".github/workflows")


class AlreadyARoot(Exception):
    """The target is a data root already. `init` creates one; it does not migrate one."""


# --------------------------------------------------------------------------- templates


def template(name: str) -> str:
    """One template's text, read from the installed package rather than from a checkout.

    Through `importlib.resources` and not `Path(__file__)`: an installed engine's templates
    travel inside the wheel, and reaching for a sibling path would work in a checkout and
    fail in exactly the installation this engine is built to be.
    """
    return resources.files(_PACKAGE).joinpath(TEMPLATES, name).read_text(encoding="utf-8")


def _toml_string(value: str) -> str:
    """One TOML basic string's contents, escaped. Not a general writer - see `entry_toml`."""
    out = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return out.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")


def _project_name(directory_name: str) -> str:
    """A distribution-shaped name from a directory name.

    The catalogue's `pyproject.toml` needs one and it is read by `uv`, so it has to be a
    legal name rather than whatever the directory is called. Lowercased, with every run of
    anything else folded to a single hyphen.
    """
    kept = [c if (c.isascii() and c.isalnum()) else "-" for c in directory_name.lower()]
    name = "-".join(part for part in "".join(kept).split("-") if part)
    return name or "radar-catalogue"


def compatible_range(engine_version: str) -> str:
    """The `requires_framework` a new catalogue is given: minor-compatible with this engine.

    Minor rather than exact, because an exact pin would make every patch release of the
    engine an edit to every catalogue; minor rather than major, because the field the engine
    silently does not know about is the failure mode this range exists to catch, and a minor
    is where a field arrives.

    An engine version this cannot parse yields a floor alone rather than a guessed ceiling: a
    range nobody can read is refused by the checker anyway, and refusing it here with a
    plausible-looking upper bound would be the worse failure.
    """
    parts = engine_version.split(".")
    try:
        major, minor = int(parts[0]), int(parts[1])
    except (IndexError, ValueError):
        return f">={engine_version}"
    return f">={engine_version},<{major}.{minor + 1}.0"


def substitutions(
    *, title: str, needle: str, requires: str, engine_version: str, today: str, project: str
) -> dict[str, str]:
    """`__MARKER__` -> value, for every template.

    MARKERS AND NOT `str.format`, for a mechanical reason: the CI template carries GitHub's
    `${{ ... }}` expressions, which `format` reads as fields and refuses. The same reason
    `render.py` substitutes its HTML template this way.

    The three free-text values are escaped as TOML strings because that is the only kind of
    file they are substituted into; the rest are versions, dates and a name already
    constrained to a safe shape.
    """
    return {
        "__TITLE__": _toml_string(title),
        "__NEEDLE__": _toml_string(needle),
        "__BOUNDARY_NOTE__": _toml_string(DEFAULT_BOUNDARY_NOTE),
        "__DEFAULT_ENVIRONMENT__": DEFAULT_ENVIRONMENT,
        "__REQUIRES_FRAMEWORK__": requires,
        "__VERSION__": INITIAL_VERSION,
        "__ENGINE_VERSION__": engine_version,
        "__DATE__": today,
        "__PROJECT_NAME__": project,
    }


def _fill(text: str, values: dict[str, str]) -> str:
    for marker, value in values.items():
        text = text.replace(marker, value)
    return text


def _write(path: Path, text: str) -> None:
    """Write LF, always, whatever platform this runs on.

    `.githooks/*` are `#!/bin/sh` scripts git executes directly, and a CR carried into the
    interpreter path fails in a way that reads as "the hook is broken" rather than "the file
    was written with the wrong line endings". Applied to every file rather than to the two
    that need it, because a generated tree with two conventions in it is a diff nobody can
    read on the machine that did not create it.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


# -------------------------------------------------------------------------------- init


def create(
    target: Path,
    *,
    title: str | None = None,
    home: Path | None = None,
    engine_version: str | None = None,
    today: datetime.date | None = None,
) -> Path:
    """Write a whole empty catalogue at `target` and return its resolved path.

    `home` and `today` are parameters rather than reads so the tree can be created against a
    synthetic machine. That is not a test hook bolted on: the profile this writes is the one
    thing here that is computed from the machine, so a function that could only ever be run
    against the real one could not be checked for the property it exists to have.
    """
    from . import __version__

    root = Path(str(target).replace("\\", "/")).expanduser().resolve()
    if (root / MARKER).is_file():
        raise AlreadyARoot(
            f"{root} already carries {MARKER}, so it is already a data root. `init` creates "
            f"a catalogue and will not write over one - there is no way for it to tell an "
            f"empty tree from a catalogue whose entries are somewhere else yet"
        )
    version = engine_version or __version__
    values = substitutions(
        title=title or root.name,
        needle=root.name,
        requires=compatible_range(version),
        engine_version=version,
        today=(today or datetime.date.today()).isoformat(),
        project=_project_name(root.name),
    )
    for relative in DIRECTORIES:
        (root / relative).mkdir(parents=True, exist_ok=True)
    for name, relative in LAYOUT:
        _write(root / relative, _fill(template(name), values))
    for relative in EXECUTABLE:
        path = root / relative
        path.chmod(path.stat().st_mode | 0o111)
    _write(
        root / "environments" / f"{DEFAULT_ENVIRONMENT}.toml",
        starter_profile(
            DEFAULT_ENVIRONMENT, discovered_paths(home or home_dir()), home or home_dir()
        ),
    )
    return root


def _enclosing_root(root: Path) -> Path | None:
    """A data root ABOVE the one just created, if any. Reported, never refused."""
    return next((p for p in root.parents if (p / MARKER).is_file()), None)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="radar init",
        description="Create a radar data root: the marker, an environment profile with this "
        "machine's discovered paths, an empty catalogue, the commit lane and a CI workflow.",
    )
    ap.add_argument(
        "directory",
        nargs="?",
        default=".",
        help="where to create the catalogue (created if absent; default: here)",
    )
    ap.add_argument(
        "--title",
        help="the title every generated artefact carries (default: the directory's name). A "
        "datum the catalogue holds, so it can be changed later in radar.toml",
    )
    args = ap.parse_args(argv)

    try:
        root = create(Path(args.directory), title=args.title)
    except AlreadyARoot as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"[FAIL] could not create a catalogue at {args.directory}: {exc}", file=sys.stderr)
        return 2

    marker = tomllib.loads((root / MARKER).read_text(encoding="utf-8"))
    print(f"[OK] data root created at {root.as_posix()}")
    for _name, relative in LAYOUT:
        print(f"       wrote {relative}")
    print(f"       wrote environments/{DEFAULT_ENVIRONMENT}.toml (paths discovered here)")

    outer = _enclosing_root(root)
    if outer is not None:
        # Reported rather than refused: the outer catalogue is untouched, and the operator
        # may well mean it. What they cannot see without being told is that a command run
        # inside the new root now finds THIS marker, because the search stops at the nearest
        # one - so the outer catalogue becomes unreachable from in here.
        print(
            f"[NOTE] {outer.as_posix()} is also a data root. The search stops at the nearest "
            f"{MARKER}, so every command run inside the new root judges the new catalogue and "
            "the enclosing one is not reachable from here"
        )

    from . import __version__ as version

    drift = framework_errors(version, str(marker.get("requires_framework") or ""))
    if drift:
        # The range above is derived from this engine's version, so the two disagree only
        # when that version could not be parsed and the range is a floor alone. Printed with
        # both numbers rather than left to surface as a mismatch on the next command.
        print(
            f"[NOTE] this engine is version {version} and the new catalogue declares "
            f"`requires_framework = {marker.get('requires_framework')!r}`, which the engine "
            f"does not satisfy ({'; '.join(drift)}). Widen the range before running another "
            "command"
        )

    print("\nnext:")
    print(f"  - cd {root.as_posix()}")
    print("  - git init && git add -A && git commit -m 'chore: the catalogue'")
    print("  - git config core.hooksPath .githooks   # arm the commit lane")
    print(f"  - git update-index --chmod=+x {' '.join(EXECUTABLE)}   # git on Windows drops it")
    print("  - uv lock                               # .github/workflows/ci.yml checks it first")
    print("  - radar add <name> --repo <url> --axis <axis> --ring observe \\")
    print("        --license <spdx> --visibility public --artifact cli \\")
    print("        --note '<one line>' --reason '<why it entered>'")
    print("  - radar gate      # 0 FAIL expected")
    print("  - radar render    # writes README.md and docs/radar/index.html")
    return 0


# --------------------------------------------------------------------------------- add


def slug(name: str) -> str:
    """The file name an entry gets. The name IS the file, so it has to survive being one."""
    kept = [c if (c.isascii() and (c.isalnum() or c in "._-")) else "-" for c in name.lower()]
    return "-".join(part for part in "".join(kept).split("-") if part)


def build_entry(args: argparse.Namespace, today: datetime.date | None = None) -> dict:
    """The entry, as the dict `validate()` judges. Optional blocks appear only when asked for.

    Absent rather than empty, throughout: an empty `[telemetry]` table is an entry that
    declares a matcher and has none, which reports a zero that reads as evidence of disuse -
    the failure the closed key set and the matcher rules both exist to prevent.
    """
    entry: dict = {
        "name": args.name,
        "repo": args.repo,
        "axis": args.axis,
        "artifact": args.artifact,
        "ring": args.ring,
        "license": args.license,
        "visibility": args.visibility,
        "note": args.note,
    }
    if args.registry:
        entry["registry"] = args.registry
    if args.eval_status:
        entry["eval_status"] = args.eval_status
    if args.telemetry_absent:
        entry["telemetry_absent"] = args.telemetry_absent

    telemetry: dict = {}
    for key, value in (
        ("match", args.telemetry_match),
        ("match_skill", args.telemetry_match_skill),
        ("match_command", args.telemetry_match_command),
    ):
        if value:
            telemetry[key] = list(value)
    if telemetry:
        date = (today or datetime.date.today()).isoformat()
        telemetry["since"] = args.telemetry_since or date
        entry["telemetry"] = telemetry

    exit_criteria = {
        "review_after_days": args.pilot_review_after_days,
        "adopt_if": args.pilot_adopt_if,
        "decline_if": args.pilot_decline_if,
    }
    if any(v is not None for v in exit_criteria.values()):
        entry["pilot_exit"] = {k: v for k, v in exit_criteria.items() if v is not None}

    history: dict = {
        "date": args.date or (today or datetime.date.today()).isoformat(),
        "ring": args.ring,
        "reason": args.reason,
    }
    if args.evidence:
        history["evidence"] = args.evidence
    entry["history"] = [history]
    return entry


def entry_errors(entry: dict) -> list[str]:
    """Everything that would stop this entry, schema and gate alike.

    `validate()` IS NOT THE WHOLE GATE, and writing an entry that only satisfies it would
    hand the operator a catalogue that fails on the very next command. Two of the gate's
    rules live outside the schema on purpose - they are judgements about the claim an entry
    makes rather than about its shape - and they are imported rather than restated, because
    a second opinion about one rule is two rules that will one day disagree.
    """
    errs = list(validate(entry))
    if missing_pilot_exit(entry):
        errs.append(
            "ring=pilot with no [pilot_exit]: say what would adopt it and what would "
            "decline it BEFORE the field data exists (--pilot-adopt-if, --pilot-decline-if, "
            "--pilot-review-after-days), or enter it at --ring observe"
        )
    if missing_telemetry(entry):
        errs.append(
            "ring=own/adopt with no [telemetry] matcher: a ring is a claim about observed "
            "state, so declare how the claim is checked (--telemetry-match, "
            "--telemetry-match-skill, --telemetry-match-command) or record why the artifact "
            "leaves no trace a session can see (--telemetry-absent)"
        )
    return errs


def entry_toml(entry: dict) -> str:
    """One entry as TOML text.

    HAND-WRITTEN AND DELIBERATELY NARROW. The engine has no runtime dependencies - that is
    what lets it be installed anywhere, and run straight off a PYTHONPATH where it cannot be
    installed at all - so there is no TOML writer to call. A general one is a small library's
    worth of edge cases; the entry schema is CLOSED, so this covers exactly its shapes and
    raises on anything else rather than emitting something that parses as the wrong thing.

    The result is read back and compared to this dict by the caller, which is what makes
    that narrowness safe to rely on.
    """
    scalars = [(k, v) for k, v in entry.items() if not isinstance(v, (dict, list))]
    lists = [(k, v) for k, v in entry.items() if isinstance(v, list) and k != "history"]
    tables = [(k, v) for k, v in entry.items() if isinstance(v, dict)]
    lines = [f"{k} = {_value(v)}" for k, v in scalars]
    lines += [f"{k} = {_value(v)}" for k, v in lists]
    for name, table in tables:
        lines += ["", f"[{name}]"]
        lines += [f"{k} = {_value(v)}" for k, v in table.items()]
    for record in entry.get("history") or []:
        lines += ["", "[[history]]"]
        lines += [f"{k} = {_value(v)}" for k, v in record.items()]
    return "\n".join(lines) + "\n"


def _value(value: object) -> str:
    if isinstance(value, bool):  # before int: bool is an int in Python
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return f'"{_toml_string(value)}"'
    if isinstance(value, list):
        return "[" + ", ".join(_value(item) for item in value) + "]"
    raise TypeError(
        f"an entry field of type {type(value).__name__} is outside the closed entry schema "
        f"this writer covers: {value!r}"
    )


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="radar add",
        description="Write one validated tool entry, with its first dated history block, "
        "into the catalogue's tools/ directory.",
    )
    ap.add_argument("name", help="the tool's name; it is also the entry's file name")
    ap.add_argument(
        "--data-root",
        help="the radar data root, instead of searching upwards for radar.toml. For CI and "
        "nothing else; it still has to name a directory that carries the marker",
    )
    required = ap.add_argument_group("the eight required fields")
    required.add_argument("--repo", required=True, help="URL, or a local path for a private entry")
    required.add_argument("--axis", required=True, choices=AXES, help="which problem it addresses")
    required.add_argument("--ring", required=True, choices=RINGS, help="how committed you are")
    required.add_argument("--license", required=True, help="SPDX identifier, as upstream states it")
    required.add_argument("--visibility", required=True, choices=("public", "private"))
    required.add_argument(
        "--artifact",
        required=True,
        choices=ARTIFACTS,
        help="what you install and where it runs - context cost and blast radius follow from it",
    )
    required.add_argument("--note", required=True, help="one line: what it is and why it is here")

    history = ap.add_argument_group("the first history block")
    history.add_argument("--reason", required=True, help="why the entry enters at this ring")
    history.add_argument("--date", help="YYYY-MM-DD (default: today)")
    history.add_argument("--evidence", help="what the reason rests on; a link, a run, a report")

    optional = ap.add_argument_group("optional fields")
    optional.add_argument("--registry", help="e.g. pypi:ruff - needed by a `latest` version policy")
    # The vocabulary is NOT repeated here. `radar_lib.validate` owns it, one of its values
    # names a particular measurement tool, and a published engine's help text is the last
    # place that name belongs - it would travel to every catalogue whose operator has never
    # heard of it. So the help says the shape and points at the schema for the rest.
    optional.add_argument(
        "--eval-status",
        metavar="STATUS",
        help="how the entry's value was established: `unmeasured` (the default) or "
        "`measured:<what was measured>`; the entry schema holds the full set",
    )
    optional.add_argument(
        "--telemetry-match", action="append", default=[], metavar="GLOB", help="MCP tool names"
    )
    optional.add_argument(
        "--telemetry-match-skill",
        action="append",
        default=[],
        metavar="GLOB",
        help="skill ids, for a tool reached through its skills",
    )
    optional.add_argument(
        "--telemetry-match-command",
        action="append",
        default=[],
        metavar="GLOB",
        help="the executable heading a shell command, for a CLI",
    )
    optional.add_argument("--telemetry-since", help="YYYY-MM-DD (default: today)")
    optional.add_argument(
        "--telemetry-absent",
        metavar="WHY",
        help="record that this artifact leaves no trace a session can see, and why",
    )
    optional.add_argument("--pilot-adopt-if", metavar="PROSE")
    optional.add_argument("--pilot-decline-if", metavar="PROSE")
    optional.add_argument("--pilot-review-after-days", type=int, metavar="N")
    optional.add_argument(
        "--force",
        action="store_true",
        help="overwrite an existing entry file; without it an existing file is refused",
    )
    return ap


def add(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = begin_command(args.data_root)

    entry = build_entry(args)
    errs = entry_errors(entry)
    if errs:
        for err in errs:
            print(f"[FAIL] {args.name}: {err}")
        print(
            "\nnothing written. An entry is validated BEFORE it lands rather than after: a "
            "rejected file left on disk is one `git add` away from being committed, and the "
            "gate would be what found it - one commit too late"
        )
        return 1

    path = tools_dir(root) / f"{slug(args.name)}.toml"
    if path.exists() and not args.force:
        print(
            f"[FAIL] {path.relative_to(root).as_posix()} exists. Edit it, or pass --force to "
            "overwrite it - an entry carries its own history, and rewriting the file from "
            "flags discards every block already in it",
            file=sys.stderr,
        )
        return 1
    path.parent.mkdir(parents=True, exist_ok=True)
    _write(path, entry_toml(entry))

    # READ BACK, and compare. The writer above is this module's own and narrow by design, so
    # the one thing that must not be assumed is that what landed parses back to what was
    # judged. A file that does not is removed rather than reported: a half-written entry the
    # operator has been told about is still a file in the catalogue.
    landed = tomllib.loads(path.read_text(encoding="utf-8"))
    if landed != entry:
        path.unlink()
        print(
            f"[FAIL] {path.name} did not read back as the entry that was validated, so it was "
            f"removed. This is a defect in the engine's entry writer, not in the arguments",
            file=sys.stderr,
        )
        return 1

    print(f"[OK] wrote {path.relative_to(root).as_posix()} - {entry['ring']} / {entry['axis']}")
    print("       radar gate      # judge it")
    print("       radar render    # write it into the catalogue's artefacts")
    return 0


if __name__ == "__main__":
    sys.exit(main())
