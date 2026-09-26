"""No tracked file points its reader at a document this repository does not ship.

WHY A TEST AT ALL. This code was written beside documents that stayed behind when it
became its own repository: a numbered log of design decisions, a planning document cut into
numbered sections, and the notes and prompts of the catalogue it grew up in. It cited them
the way code cites an issue tracker: a rule in a comment, a docstring or a message,
followed by the record, the section or the file that decided it. None of them is part of
this repository. A reader meets a rule resting on an authority that exists nowhere in the
package, and the pointer tells them only that the reason is somewhere they cannot go. So
every such citation was rewritten to say the rule, or the reason, where the pointer stood.

That rewrite decays silently. Citing those documents is the habit the code was written in,
so the next change written the same way brings a citation back, and nothing else in this
repository would notice: the publication check holds the tree to the names it is given and
to path forms, and a record number, a section number or the name of an absent document is
none of those. This is the check that notices.

IT MATCHES FORMS, NEVER NAMES. A list of the absent documents by name would be one more
thing that is true of one catalogue and silently wrong for the next, so each pattern is
the shape a citation takes: a decision record by its prefix or by its path, a numbered
section, and a capitalised Markdown document that this tree does not ship and the engine
does not itself read or write.

THE WHOLE TRACKED TREE, with no exclusion. The changelog is included on purpose: a
citation in a changelog entry points at the same absent document as one in a comment, and
rewriting one to state the rule changes no fact about what a release shipped.

A TREE THAT CANNOT BE LISTED FAILS rather than passing. An empty file list is exactly what
a clean result looks like, so a scan that read nothing must not be allowed to report one.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# The forms such a citation takes, each with what it points at. Built from pieces so that
# this module, which is itself a tracked file, carries the patterns and not an instance of
# any of them. The section sign is written as its UTF-8 bytes for the same reason.
_FORMS = (
    (re.compile(rb"\bAD" + rb"Rs?\b"), "a decision record"),
    (re.compile(rb"\bdecisions" + rb"/"), "a decision record, by its path"),
    (re.compile(rb"\xc2\xa7" + rb"\s*[0-9]+"), "a numbered section of a planning document"),
)

# A capitalised Markdown document, by file name.
_DOCUMENT = re.compile(rb"\b[A-Z][A-Z0-9_-]*[A-Z0-9]\.md\b")

# The capitalised documents a reader meets on disk without this repository shipping them,
# because the engine itself reads or writes them in a catalogue or a data plane. The ones
# this repository does ship are added from `git ls-files` at run time.
_ENGINE_DOCUMENTS = frozenset(
    {
        b"CLAUDE.md",  # a data-plane execution file the gate reads
        b"INDEX.md",  # the feedback index `sync-feedback` regenerates
        b"PUBLIC.md",  # the public projection `render` writes
    }
)


def tracked_files() -> list[str]:
    """Every file git tracks in this checkout, by repo-relative path."""
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO,
        capture_output=True,
        check=True,
    ).stdout
    return [p for p in out.decode("utf-8").split("\0") if p]


def test_no_tracked_file_points_at_a_document_this_repository_does_not_ship():
    files = tracked_files()
    # The guard is only as good as the list it walks.
    assert "pyproject.toml" in files and "src/stack_radar/gate.py" in files, (
        "`git ls-files` did not list this repository's own files, so the citation guard "
        "would scan nothing and pass - which is the wording of a clean scan"
    )
    shipped = {Path(rel).name.encode() for rel in files}
    known = shipped | _ENGINE_DOCUMENTS
    hits = []
    for rel in files:
        path = REPO / rel
        if not path.is_file():
            continue
        for lineno, line in enumerate(path.read_bytes().splitlines(), 1):
            where = f"{rel}:{lineno}"
            for pattern, points_at in _FORMS:
                for m in pattern.finditer(line):
                    hits.append(f"{where}: {m.group().decode()} ({points_at})")
            for m in _DOCUMENT.finditer(line):
                if m.group() not in known:
                    hits.append(f"{where}: {m.group().decode()} (a document not shipped here)")
    assert not hits, (
        f"{len(hits)} citation(s) of a document this repository does not ship - state the "
        "rule or the reason where the pointer stands: "
        + "; ".join(hits[:10])
        + (f"; and {len(hits) - 10} more" if len(hits) > 10 else "")
    )
