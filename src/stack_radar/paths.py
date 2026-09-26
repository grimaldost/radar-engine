"""Where the catalogue is. The data root, and the directories it implies.

Split out of `radar_lib` when the engine became its own repository: the root
resolver is what every command needs FIRST and what nothing else depends on, so it
is the one module here that imports no sibling. An installed engine has no
`__file__` anywhere near the data it reads, and this is the module that says so.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import NamedTuple

# --------------------------------------------------------------------- the data root

MARKER = "radar.toml"

# The env var and the flag, in the order they are consulted. Both exist for CI and for
# nothing else: the ordinary way to reach a data root is to stand in
# one. Each still has to name a directory that carries the marker, so a typo fails
# naming the file rather than resolving to a plausible wrong tree.
ENV_VAR = "RADAR_DATA_ROOT"
FLAG = "--data-root"


class DataRootNotFound(Exception):
    """No radar data root: no marker above the working directory, none named.

    Its own exception type because the callers need to spend a distinct EXIT CODE on it.
    A missing data root is not a finding about a catalogue - it is the absence of one -
    and a gate that answered `1` for both would make "the invariant failed" and "there
    was nothing to check" the same shell condition, which is the class of confusion the
    skipped-check reporting in gate.py exists to prevent.
    """


class DataRoot(NamedTuple):
    path: Path
    # How it was found, printed on the first line of every command. Not decoration: with
    # three ways in, "which tree did that just judge, and why that one" is otherwise
    # answerable only by re-deriving the resolution by hand.
    source: str


def _flag_value(argv: list[str] | None = None) -> str | None:
    """`--data-root X` or `--data-root=X`, read straight from argv.

    Read here rather than taken from each parser because the commands are not uniform:
    `render.py` has no argparse at all, and threading an override through every call
    site would mean the flag worked on some commands and not others. The argparse
    commands still DECLARE the option - argparse rejects what it does not know - and
    both readings see the same string.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    for n, arg in enumerate(args):
        if arg == FLAG and n + 1 < len(args):
            return args[n + 1]
        if arg.startswith(FLAG + "="):
            return arg.split("=", 1)[1]
    return None


def _marked(given: str, source: str) -> DataRoot:
    path = Path(str(given).replace("\\", "/")).expanduser()
    if not (path / MARKER).is_file():
        raise DataRootNotFound(
            f"{source} names {path}, which holds no {MARKER} - the marker is what makes "
            "a directory a radar data root, and an override does not exempt it"
        )
    return DataRoot(path.resolve(), source)


def resolve_data_root(override: str | None = None, *, start: Path | None = None) -> DataRoot:
    """Where the catalogue is, and how that was decided.

    The search is the marker, the way git finds `.git`: up from the working directory
    until a `radar.toml` turns up. It replaces `Path(__file__).parent.parent`, which
    answered a different question - where the SCRIPT is - and happened to give the same
    answer only while the engine and the catalogue were one repository. An installed
    engine has no `__file__` anywhere near the data it reads.
    """
    given = override if override is not None else _flag_value()
    if given:
        return _marked(given, FLAG)
    from_env = os.environ.get(ENV_VAR)
    if from_env:
        return _marked(from_env, ENV_VAR)
    here = (Path(start) if start else Path.cwd()).resolve()
    for cand in (here, *here.parents):
        if (cand / MARKER).is_file():
            return DataRoot(cand, MARKER)
    raise DataRootNotFound(
        f"no {MARKER} in {here} or any directory above it - a radar command reads the "
        f"catalogue it is standing in. Run it from inside the data root, or name one "
        f"with {FLAG} / {ENV_VAR}"
    )


def data_root(override: str | None = None) -> Path:
    """The data root, resolved. Never cached: a test that moves the tree moves it."""
    return resolve_data_root(override).path


# The six directories the root implies. Functions rather than constants because a
# constant is evaluated at IMPORT time, which would resolve the root - and therefore
# fail, in a directory with no marker - merely because a module was imported. Import has
# to stay free of it: `--help` must work, and the import smoke proof in
# tests/test_data_root.py depends on nothing being resolved before a command runs.
def tools_dir(root: Path | None = None) -> Path:
    return (root or data_root()) / "tools"


def tools_local_dir(root: Path | None = None) -> Path:
    """Machine-local tool entries, gitignored, for in-house tooling whose NAME must not
    travel in a personal repo (a private GitHub repo is still a third party holding the
    name). The tool-level counterpart of environments/*.local.toml. Content that
    legitimately refers to such a tool is mapped to a placeholder on ingest - see
    redact.py.

    THE TRAP, because it is not obvious: README.md and docs/radar/*.html are COMMITTED
    and are rendered from load_tools(). A local entry that reached a renderer would be
    written straight into a tracked file and the gitignore would have bought nothing.
    Every entry loaded from here carries `_local = True`, and render.py drops those
    before writing any tracked artefact. That is the whole safety mechanism — do not
    remove the flag.
    """
    return (root or data_root()) / "tools.local"


def snap_dir(root: Path | None = None) -> Path:
    return (root or data_root()) / "snapshots"


def env_dir(root: Path | None = None) -> Path:
    return (root or data_root()) / "environments"


def feedback_dir(root: Path | None = None) -> Path:
    return (root or data_root()) / "feedback"


def field_dir(root: Path | None = None) -> Path:
    return (root or data_root()) / "field"
