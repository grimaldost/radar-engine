"""Adoption gate: schema, evidence, licence, staleness/astroturf, and the invariant.

Encodes the catalog's backing gate as code:
- downloads are the decisive signal; stars are never proof;
- registry identity comes from repo metadata, never name-matching;
- ring transitions carry evidence; adopt/pilot entries must not go stale;
- the version sites agree;
- and the invisible-hand invariant holds: no data-plane file references
  this control plane.
Exit 1 on FAIL findings (schema errors, archived repos, invariant breaches);
WARN/FLAG/NOTE are informational.

TWO CLASSES OF CHECK, and the difference decides what a green run means.

Most of what this script asks is answerable from the repository alone: schema, rings,
evidence links, licences, staleness against the committed snapshot, telemetry keys,
pilot_exit rules, version sites. Two are not - the invariant scan and the
scope-leak scan - because each needs something that exists on a MACHINE: the data plane
(`~/.claude`, the governed tool worktrees) and the gitignored `tools.local/` entries.

In CI neither exists. `environments/personal.toml` points at paths that are absent on a
runner by construction, so the old code path printed "invariant holds - 0 data-plane
files scanned" and the run went green - a vacuous pass with the exact wording of a real
one. So absence is DETECTED and NAMED: the run says which checks did not run and why,
and everything repo-only still runs and can still fail. A gate that cannot say what it
skipped is a gate that will one day skip everything and report success.
"""

from __future__ import annotations

import argparse
import datetime
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import NamedTuple

from .paths import MARKER, data_root
from .radar_lib import (
    TELEMETRY_KEYS,
    begin_command,
    latest_snapshot,
    load_environments,
    load_marker,
    load_tools,
    missing_pilot_exit,
    missing_telemetry,
    unknown_telemetry_keys,
    validate,
    version_site_errors,
)
from .redact import has_scoped, scoped_terms

# The invariant's own falsification test, as code. A data-plane file naming the control
# plane would make a working session depend on this repo's presence.
#
# THE NEEDLE IS NOT A LITERAL HERE ANY MORE. It used to be
# `INVARIANT_NEEDLES = ("stack-radar",)`, which was true of exactly one machine and
# invisible when it stopped being true. Two ways for it to go wrong, both silent and both
# printing "holds": a renamed bank is scanned for a name nothing carries, and - once the
# engine ships under `stack-radar` - every legitimate mention of a published tool is a
# FAIL, which is how an operator learns to skip the line. So the names come from the
# catalogue that knows them, `radar.toml [invariant].needles`, and the code contributes
# only what it can derive: the data root's own absolute path.
# Session transcripts legitimately contain radar paths (the radar is developed in
# sessions); they are a log, not a binding, so they are out of scope by construction.
#
# THESE APPLY TO THE `<claude_home>` DIRECTORY ROOTS ONLY, which are walked. The governed
# worktrees are enumerated by `git ls-files` now, so what they exclude comes from their
# own `.gitignore` — which is half the reason for the switch. The predicate below matches
# a path COMPONENT at any depth, so an entry here deletes every directory of that name in
# every root it is applied to. `feedback` is deliberately NOT in this set for exactly that
# reason: it would take a governed worktree's own `docs/feedback/` with it. It is
# excluded as one named root
# instead — see CLAUDE_HOME_OMITTED.
INVARIANT_SKIP_DIRS = {
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    "projects",
    "todos",
    "shell-snapshots",
    "statsig",
}
INVARIANT_SUFFIXES = {
    ".md",
    ".toml",
    ".json",
    ".py",
    ".js",
    ".ts",
    ".sh",
    ".ps1",
    ".txt",
    ".yaml",
    ".yml",
}
# The `--fast` ceiling, per root — and NOT the default any more. As the default it made
# the gate's own sentence false: on 2026-09-05 the scan reached 6368 of 18772 eligible
# files (34%) and printed "holds" with no caveat, which is a partial scan wearing a
# complete scan's words. The default is complete; `--fast` opts back into the
# cap, and every root it cuts prints TRUNCADO so the two runs can never be confused.
INVARIANT_MAX_FILES = 6000

# THE THREE FILES THE ENGINE'S NAME MAY NOT APPEAR IN. Everywhere else
# in the data plane the engine is an ordinary published tool and naming it is ordinary -
# a note, a manifest, a shell history. These three are what makes a session RUN
# something, so they are where the rule that the radar is never an execution
# dependency stays checkable by grep rather than by reading. Mentioning the
# engine is fine; wiring a hook to it is not.
#
# Matched by basename, except the hooks file, which is only that file in that directory:
# `hooks.json` alone is a name anything could take.
EXECUTION_FILE_NAMES = ("settings.json", "CLAUDE.md")
EXECUTION_FILE_SUFFIX = "hooks/hooks.json"

# The data plane inside `<claude_home>`: an allow-list, never a walk of the whole
# directory, because `~/.claude` also holds transcripts, caches and shell snapshots, which
# are logs rather than bindings. A name declared here that does not exist yet is skipped
# in silence — `dispatch-overlay.toml` and `dispatch-table.json` arrive with the dispatch
# layer, and declaring them before they exist is what makes the scan reach them
# on the day they land. The overlay matters most: this repository writes it, and a
# renderer that leaks the control plane's identity is only caught if the scan reads what
# it wrote.
CLAUDE_HOME_SUBPATHS = (
    "settings.json",
    "CLAUDE.md",
    "skills",
    "plugins",
    "feedback-targets.toml",
    "dispatch-overlay.toml",
    "dispatch-table.json",
    # Maintained by hand, outside every repository: the one data-plane file no CI reaches.
    "model-mirrors.toml",
)

# What the allow-list leaves out ON PURPOSE, with its reason, printed on every run. Same
# device as `[invariant].exempt` in the marker, and for the same reason: a declared
# omission keeps
# arguing for itself, while a name that is simply absent from a tuple is indistinguishable
# from an oversight.
#
# ASCII only, like every other printed string here: this console is cp1252, and a run
# whose note comes out mojibake is a run nobody reads to the end.
CLAUDE_HOME_OMITTED = {
    "feedback": (
        "the mirrored feedback inbox - a report is a LOG of "
        "what a session wrote, the same kind of thing as the transcripts this scan "
        "already skips, "
        "and the reports that carry the name are mirrored records rather than bindings. "
        "Excluded as this one root and not as a path component: the component predicate "
        "matches at any depth and would drop a governed worktree's own `docs/feedback/` in the "
        "very change that promises complete coverage."
    ),
}


class Root(NamedTuple):
    """One place the invariant scan reads, and how it is enumerated.

    `kind` is not cosmetic: a worktree is asked of git and a failure there is a FAIL,
    while a `<claude_home>` root is not a checkout, is walked, and is never a FAIL for
    not being one.
    """

    path: Path
    origin: str  # what declared it, for the per-root line: a tool name, or the marker
    kind: str  # "file" (read whole) | "dir" (walked) | "worktree" (git ls-files)


# KNOWN EXEMPTIONS live in the marker, not here: `radar.toml [invariant.exempt]`, read by
# `declared_exempt()` below. The reasoning that used to sit beside the constant is now in
# that function's docstring and in the marker's own comment, because the list is data. What
# stays true wherever it lives: an exemption is not a general
# escape hatch: an entry here prints as a NOTE on every run precisely so it cannot
# quietly become permanent, and the rule stays "zero references" for everything else.
#
# The distinction the grep cannot make: a file that lists the needle among terms it
# FORBIDS is asserting the radar's absence, not depending on its presence. That reading
# is true here and would be an easy lie elsewhere, so exemptions are named one at a
# time rather than inferred from context.

COPYLEFT = ("GPL", "LGPL", "AGPL", "CC-BY-SA")
SOURCE_AVAILABLE = ("Elastic", "BUSL", "FSL", "PolyForm")
STALE_DAYS = 180
ASTROTURF_RATIO = 0.05  # downloads/month below 5% of stars, with stars > 5k -> flag
ASTROTURF_MIN_STARS = 5000


def profile_paths(override: str | None = None) -> list[dict[str, str]]:
    """Each committed profile's [paths], forward-slashed, with claude_home overridden.

    A `repo` or a [feedback].worktree is written with the profile's placeholders
    (`{documents}/<tool>`), because one entry has to resolve on every machine. So a root
    can only be resolved PER PROFILE, and the same entry legitimately resolves on one
    profile and nowhere on the others - which is the normal case, not an error.
    """
    out: list[dict[str, str]] = []
    for env in load_environments():
        paths = {
            str(k): str(v).replace("\\", "/")
            for k, v in (env.get("paths") or {}).items()
            if isinstance(v, str) and v
        }
        if override:
            paths["claude_home"] = str(override).replace("\\", "/")
        out.append(paths)
    if override and not out:
        out.append({"claude_home": str(override).replace("\\", "/")})
    return out


def claude_homes(override: str | None = None) -> list[Path]:
    """Every profile's claude_home, or the single override, as paths (existing or not)."""
    if override:
        return [Path(str(override).replace("\\", "/"))]
    return [Path(p["claude_home"]) for p in profile_paths() if p.get("claude_home")]


def expand(value: str, paths: dict[str, str]) -> str:
    """Fill the profile's `{key}` placeholders, the way an [install] command is filled.

    An unknown key is left written as it stands rather than guessed at. That is what
    makes it reportable: `{documents}/<tool>` with no `documents` in the profile is not a
    directory, so the entry lands in the unresolved list with the string it tried.
    """
    out = str(value).replace("\\", "/")
    for key, v in paths.items():
        out = out.replace("{" + key + "}", v)
    return out


def declared_needles(marker: dict) -> list[str]:
    """`radar.toml [invariant].needles`, cleaned. The bank's own name(s), as data.

    Empty is a legal catalogue and a WEAKER scan, which is the whole reason the caller
    says so out loud: the data root's absolute path is still a needle, so the run still
    prints "holds" and the sentence is still true - just about half of what the reader
    assumes it is about.
    """
    raw = (marker.get("invariant") or {}).get("needles") or []
    if isinstance(raw, str):  # one name written without the brackets
        raw = [raw]
    return [str(n).strip() for n in raw if isinstance(n, str) and str(n).strip()]


def declared_exempt(marker: dict) -> tuple[dict[str, str], list[str]]:
    """`radar.toml [invariant].exempt`, as {path-suffix: reason}.

    Returns (exemptions, notes-about-what-was-discarded). A SECOND RETURN VALUE rather than
    a quietly smaller dict, for the reason every check in this file gives one: a declaration
    the code dropped looks exactly like a declaration nobody wrote, and the operator finds
    out when the gate goes red on the file they thought they had exempted.

    THE SAME MOVE THE NEEDLES MADE INTO `radar.toml`, one step later. This list used to be
    a constant here, and a constant was the wrong home for exactly the reason the needles
    were: it is true of one estate and invisible on the day it stops being. Worse, it had
    to spell out a path inside a repository that is not this one - so a published engine
    carried the name of a private sibling in its source, which is what the publication
    check exists to refuse.

    The distinction the grep cannot make: a file that lists the needle among terms it
    FORBIDS is asserting the radar's absence, not depending on its presence. That reading
    is true of a given file and would be an easy lie elsewhere, so exemptions are named one
    at a time, by the catalogue that knows them, rather than inferred from context.

    A key is an `endswith` suffix. Prefer dir+basename over a worktree-rooted path when the
    same module is served from more than one place - a worktree AND every versioned copy in
    a plugin cache, say - or the key matches the original and misses the copies.

    Empty is legal and means no exemption, which is the stricter reading.
    """
    raw = (marker.get("invariant") or {}).get("exempt") or {}
    if not isinstance(raw, dict):
        return {}, [
            f"[NOTE] invariant: `[invariant].exempt` is {type(raw).__name__}, not a table - "
            'IGNORED. It is written `[invariant.exempt]` with one `"path/suffix" = '
            '"reason"` row per exemption. No file is exempt while it is the wrong shape, so '
            "the scan is STRICTER than declared rather than looser, and says so here."
        ]
    kept: dict[str, str] = {}
    dropped: list[str] = []
    for k, v in raw.items():
        key = str(k).strip() if isinstance(k, str) else ""
        if not key:
            dropped.append("[NOTE] invariant: an exemption with an empty key - IGNORED.")
            continue
        if not isinstance(v, str):
            # A nested table is the typo this catches: `[invariant.exempt.foo]` parses, and
            # without this branch it became a LIVE exemption keyed `foo` whose reason was the
            # repr of a dict - an accountability mechanism turned into noise by a typo.
            dropped.append(
                f"[NOTE] invariant: exemption {key!r} has a {type(v).__name__} where a reason "
                "string belongs - IGNORED. A nested `[invariant.exempt.<key>]` table is the "
                "usual cause; the row is written inside `[invariant.exempt]`, not under it."
            )
            continue
        reason = v.strip()
        if not reason:
            dropped.append(
                f"[NOTE] invariant: exemption {key!r} declares no reason - IGNORED. The reason "
                "is the whole accountability of an exemption; it is printed on every run."
            )
            continue
        kept[key] = reason
    return kept, dropped


def _same_path(a: Path, b: Path) -> bool:
    """normcase, not lower(): case folding is right on Windows and wrong on POSIX."""
    return os.path.normcase(str(a.resolve())) == os.path.normcase(str(b.resolve()))


def same_checkout(declared: str, other: str, profiles: list[dict[str, str]]) -> bool:
    """Do two DECLARED paths name the same directory under some profile?

    Both sides are written with the profile's placeholders - the engine entry's `repo` is
    `{documents}/stack-radar` and `[publish].framework_worktree` is
    written the same way - so a literal string compare is not merely fragile, it never
    matches at all. Expand under each profile and resolve both sides.

    Existence is deliberately NOT required. The comparison answers "are these the same
    checkout", which is true on a machine that has not cloned it, and the publication
    check's exception has to hold there too or it holds only where it is not needed.
    """
    if not declared or not other:
        return False
    if str(declared).startswith(("http://", "https://", "git@")):
        return False
    for paths in profiles:
        if _same_path(Path(expand(str(declared), paths)), Path(expand(str(other), paths))):
            return True
    return False


def framework_entry(
    tools: list[dict], worktree: str, profiles: list[dict[str, str]]
) -> dict | None:
    """The `own` entry whose `repo` IS the declared engine checkout, or None.

    BY IDENTITY, NEVER BY NAME, and that is the load-bearing word. The engine is itself an
    `own` entry in this catalogue and it is named after itself, so a
    name-matching exception would exempt any entry that happened to be called the same
    thing - and would still have to be told the name from somewhere. Matching on `repo`
    means one fact decides it and there is no second key to drift.

    The consequence binds the entry itself: while the exception
    matches on `repo`, the engine's entry stays `visibility = "private"`, because the
    public-entry rule forces `repo` to a URL and a URL is not a checkout.
    """
    for t in tools:
        if t.get("ring") != "own":
            continue
        if same_checkout(str(t.get("repo") or ""), worktree, profiles):
            return t
    return None


def is_execution_file(p: Path) -> bool:
    """One of the three files the engine's name is kept out of."""
    return p.name in EXECUTION_FILE_NAMES or p.as_posix().endswith(EXECUTION_FILE_SUFFIX)


def data_plane_roots(
    tools: list[dict], claude_home: str | None = None
) -> tuple[list[Root], list[str]]:
    """The places the invariant covers, AND the `own` entries that did not become one.

    Empty roots means there is no data plane reachable from here - a CI runner, or a
    fresh clone that has not been bootstrapped. The caller must treat that as "not
    checked", never as "checked and clean"; main() does.

    The second half of the return value exists because the first half used to lie by
    omission. `{documents}` was never expanded here, so `Path("{documents}/<tool>")` was
    not a directory, and seven of the eight governed worktrees dropped out of the scan
    without a word - the gate said "holds" over one of them. A root that cannot be
    resolved now has to say so by name, so that an absent root shows up in the note
    instead of simply not existing.
    """
    profiles = profile_paths(claude_home)
    here = data_root()
    roots: list[Root] = []
    seen: set[str] = set()

    def add(path: Path, origin: str, kind: str) -> None:
        # Two profiles can name the same literal directory, and an entry can repeat one.
        # normcase, not lower(): case folding is right on Windows and wrong on POSIX.
        key = os.path.normcase(str(path.resolve()))
        if key not in seen:
            seen.add(key)
            roots.append(Root(path, origin, kind))

    for base in claude_homes(claude_home):
        for sub in CLAUDE_HOME_SUBPATHS:
            p = base / sub
            if p.exists():
                add(p, f"<claude_home>/{sub}", "file" if p.is_file() else "dir")

    unresolved: list[str] = []
    for t in tools:
        # Every root of this kind comes from an `own` entry: [feedback] is only valid
        # there (radar_lib.validate), and a `repo` is only read as a worktree there.
        if t.get("ring") != "own":
            continue
        name = str(t.get("name") or t.get("_file"))
        where = str(t.get("_file"))
        declared = (t.get("feedback") or {}).get("worktree") or t.get("repo")
        if not declared:
            unresolved.append(
                f"{name} ({where}): neither `repo` nor [feedback].worktree names a "
                "local checkout, so there is no tree to scan"
            )
            continue
        if str(declared).startswith(("http://", "https://", "git@")):
            unresolved.append(
                f"{name} ({where}): `repo` is a remote URL only and the entry declares no "
                "[feedback].worktree, so there is no local tree to scan"
            )
            continue
        tried: list[str] = []
        resolved = is_self = False
        for paths in profiles:
            cand = Path(expand(str(declared), paths))
            tried.append(cand.as_posix())
            if not cand.is_dir():
                continue
            if cand.resolve() == here:
                is_self = True
                continue
            resolved = True
            add(cand, name, "worktree")
        if resolved:
            continue
        if is_self:
            unresolved.append(
                f"{name} ({where}): resolves to this repository - the control plane is "
                "not part of the data plane it governs"
            )
        else:
            unresolved.append(
                f"{name} ({where}): {declared} is not a directory in any profile "
                f"(tried {', '.join(sorted(set(tried)))})"
            )
    return roots, unresolved


def git_ls_files(root: Path) -> list[Path] | None:
    """Every file git tracks under `root`, plus every un-ignored file it does not yet.

    None - never an empty list - when git could not answer at all: git absent, or `root`
    is not a checkout. The distinction is the whole point of the return type. A root that
    cannot be enumerated and comes back as `[]` prints "0 scanned of 0 eligible", which
    is the wording of a clean root; the caller turns None into a FAIL naming the root.

    `--others --exclude-standard` is not padding. With `--cached` alone a NEW file is
    invisible until it is committed, so a check first fires on the commit AFTER the one
    that introduced the leak - which is exactly how `tracked_files()`'s own module
    slipped through its first run.
    """
    try:
        out = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return [root / n for n in out.stdout.split(chr(0)) if n]


def walk_files(root: Path) -> list[Path]:
    """Every file under a `<claude_home>` root. These are not checkouts: there is no git
    to ask, so the directory is walked and INVARIANT_SKIP_DIRS carries the exclusions."""
    files: list[Path] = []
    for p in root.rglob("*"):
        if any(part in INVARIANT_SKIP_DIRS for part in p.parts):
            continue
        if p.is_file():
            files.append(p)
    return files


def check_invariant(
    roots: list[Root],
    names: list[str],
    framework_name: str | None = None,
    fast: bool = False,
    exempt: dict[str, str] | None = None,
) -> list[str]:
    """Grep the data plane for references to this repo. Returns FAIL messages.

    Takes the roots rather than computing them so the caller decides - and can see -
    whether there was a data plane to scan at all. It takes `names` for the same reason:
    they come from `radar.toml`, and a function that read them itself
    would be a second reader of the marker with its own idea of what an empty list means.

    `framework_name` is the engine's, and it is a needle in the three EXECUTION files
    only. None means the engine could not be identified - until somebody writes its
    `own` entry the catalogue has none - and the caller says so rather than letting the
    narrower check disappear into a green run.

    WHAT THE NOTE SAYS, and why it is a table rather than a word. The old note printed
    one number for the whole scan, so a root that contributed nothing looked exactly like
    a root that was clean, and the 34% coverage the per-root ceiling produced was
    invisible in the sentence "holds". Every root now prints scanned/eligible under its
    own name, `--fast` marks the ones it cut with TRUNCADO, and a worktree git cannot
    enumerate is a FAIL naming the root rather than a quiet "0 of 0".
    """
    needles = tuple(names) + (data_root().as_posix(),)
    exempt_map = exempt or {}
    fails: list[str] = []
    exempted: list[str] = []
    lines: list[str] = []
    scanned_total = eligible_total = 0
    started = time.monotonic()
    for root in roots:
        if root.kind == "worktree":
            found = git_ls_files(root.path)
            if found is None:
                fails.append(
                    f"[FAIL] invariant: data-plane root could not be "
                    f"enumerated - `git ls-files` failed in {root.path} ({root.origin}). "
                    "git is absent, or the directory is not a checkout; either way the "
                    "root was NOT scanned, which is not the same as scanning it clean"
                )
                lines.append(f"[NOTE]   {root.origin}: NOT ENUMERABLE - {root.path}")
                continue
            candidates = found
        elif root.kind == "dir":
            candidates = walk_files(root.path)
        else:
            # A file sub-path was named one by one, so it is eligible whatever it is
            # called: the allow-list already decided it belongs in the scan.
            candidates = [root.path]
        eligible = [
            p for p in candidates if root.kind == "file" or p.suffix.lower() in INVARIANT_SUFFIXES
        ]
        batch = eligible[:INVARIANT_MAX_FILES] if fast else eligible
        cut = " TRUNCADO (--fast)" if len(batch) < len(eligible) else ""
        lines.append(
            f"[NOTE]   {root.origin}: {len(batch)} scanned / {len(eligible)} eligible"
            f" - {root.path}{cut}"
        )
        scanned_total += len(batch)
        eligible_total += len(eligible)
        for p in batch:
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            # The engine's name joins the needles for THIS FILE only when the file is one
            # of the three that make a session run something.
            here = needles + ((framework_name,) if framework_name and is_execution_file(p) else ())
            hit = next((n for n in here if n in text), None)
            if not hit:
                continue
            posix = p.as_posix()
            hit_exempt = next((k for k in exempt_map if posix.endswith(k)), None)
            if hit_exempt:
                exempted.append(hit_exempt)
                continue
            fails.append(
                f"[FAIL] invariant: data-plane file references the control plane ({hit!r}): {p}"
            )
    elapsed = time.monotonic() - started
    for name, why in sorted(CLAUDE_HOME_OMITTED.items()):
        print(f"[NOTE] invariant: <claude_home>/{name} is outside the scan by declaration - {why}")
    for e in sorted(set(exempted)):
        print(f"[NOTE] invariant exemption in effect - {e}: {exempt_map[e]}")
    verdict = (
        "holds"
        if not fails
        else f"{len(fails)} finding(s)"  # never the word "holds" over a scan that bit
    )
    print(
        f"[NOTE] invariant: {verdict} - {scanned_total} of {eligible_total} "
        f"eligible data-plane files scanned across {len(roots)} root(s) in {elapsed:.1f}s, "
        f"{len(fails)} reference(s), {len(set(exempted))} exemption(s)"
    )
    for line in lines:
        print(line)
    return fails


def tracked_files(root: Path | None = None) -> list[Path] | None:
    """Every file git tracks, plus every un-ignored file it does not yet.

    `--others --exclude-standard` is not padding. With `--cached` alone a NEW file is
    invisible until it is committed, so the check first fires on the commit AFTER the
    one that introduced the leak - which is exactly how this function's own module
    slipped through its first run. Ignored files stay out: tools.local/ is where the
    name is supposed to live.

    Asking git rather than globbing directories is the point:
    the first version of check_scope_leak listed the directories it thought mattered
    (the rendered pages, the entries and the written decisions) and therefore never looked at
    scripts/ or field/ - both tracked, both carrying an in-house name, both pushed.
    A hand-maintained list of where to look for a leak goes stale the first time a
    directory is added, and nothing reports that it has.

    The enumeration itself lives in `git_ls_files`, which the invariant scan shares, and
    `None` - never an empty list - travels all the way out of here too. That used to be
    the one place the silent empty list survived, excused because the root was the
    repository the script was running out of: git could hardly fail to answer about the
    tree the module itself was loaded from. Resolving the root from a marker removed that
    excuse by removing the coupling. The root is now whatever `radar.toml` was found by
    walking up from the working directory, which can perfectly well be a directory git
    knows nothing about - an exported tree, a bundle unpacked without `.git`, a
    `--data-root` pointed at a copy. There the old return said "0 tracked files, no
    leaks", which is the wording of a clean scan produced by not scanning. The caller
    turns `None` into a FAIL naming the root, exactly as the invariant scan does for a
    worktree.
    """
    return git_ls_files(root or data_root())


def check_scope_leak(tools: list[dict]) -> list[str]:
    """No tracked file may name a machine-local tool - in its content or its filename.

    The gitignore and render.py's include_local=False stop scoped ENTRIES from reaching
    a tracked file. Neither stops PROSE: a `note` or a history `reason` in a committed
    entry that mentions an in-house tool by name gets rendered into README.md and
    pushed. That is not hypothetical - it happened while correcting evidence counts on
    2026-07-26, when two committed entries cited the corporate project their number came
    from, and the names appeared in README.md two commits after the isolation was built.

    So the isolation is verified rather than assumed, on the same principle as the
    invariant scan: an invariant that nobody verifies does not exist.

    Two things changed on 2026-08-24, both because the check was weaker than it read:

    1. It scanned a hand-listed subset of directories and missed 197 tracked files -
       the whole feedback archive, the generated field reports (a session slug carries
       the project path it ran in), and two of these scripts' own docstrings. It now
       asks git what is tracked.
    2. It only looked at file CONTENT. Eighteen reports carried the name in their own
       FILENAME, which leaks from a directory listing with the file unopened.

    Scope is `_local` entries only. An entry in tools/ with `environments = ["corp"]`
    took the other side of the trade-off - limited to one environment, but not secret:
    it travels, and its name is in the repo by design - so flagging it would mean
    failing on a name committed one directory away, permanently.
    """
    scoped = {str(t["name"]) for t in tools if t.get("_local")}
    if not scoped:
        return []
    terms = scoped_terms(tools)
    missing = [
        str(t["name"])
        for t in tools
        if t.get("_local") and not str(t.get("redact_as") or "").strip()
    ]
    for name in missing:
        print(
            f"[WARN] {name}: machine-local entry declares no `redact_as`; ingestion "
            "will fall back to a generic placeholder and the corpus loses which tool "
            "it referred to"
        )
    root = data_root()
    found = tracked_files(root)
    if found is None:
        return [
            f"[FAIL] scope leak: the data root could not be enumerated - `git ls-files` "
            f"failed in {root}. git is absent, or the root is not a checkout; either way "
            "the tracked files were NOT scanned, which is not the same as scanning them "
            "clean"
        ]
    fails = []
    for f in found:
        rel = f.relative_to(root)
        hit_name = has_scoped(str(rel), terms)
        if hit_name:
            fails.append(
                f"[FAIL] scope leak: tracked FILENAME contains {hit_name[0]!r}: {rel} "
                "- run `radar redact-backfill` to map it out"
            )
        if not f.is_file():
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        hit = has_scoped(text, terms)
        if hit:
            fails.append(
                f"[FAIL] scope leak: tracked file names the scoped tool {hit[0]!r}: "
                f"{rel} - rewrite the prose to describe it without naming it, or run "
                "`radar redact-backfill` for generated/archived content"
            )
    return fails


# --------------------------------------------------------------- the publication check


class Forbidden(NamedTuple):
    """One string that must not appear in the engine's checkout, and why not.

    The reason travels with the term because the check's whole value is in the FAIL line:
    "`widget` appears in README.md" is a puzzle, while "`widget` is the name of an `own`
    catalogue entry (tools/widget.toml)" says both what went wrong and which file to go
    and change.
    """

    term: str
    why: str


# The two FORM rules, which are the only part of this check the engine's own CI could
# ever run: they name nothing private, so they are shapes rather than
# a list. `Path.home()` is how a machine path gets into code without a literal, and
# `bootstrap.py` is the one module whose JOB is to find the home directory.
PATH_HOME = re.compile(r"Path\s*\.\s*home\s*\(\s*\)")
PATH_HOME_EXEMPT = "bootstrap.py"
# `C:/Users/<segment>`, either separator. The segment is captured so it can be judged:
# the literal is only a leak when it names a real account.
USER_LITERAL = re.compile(r"(?i)c:[\\/]users[\\/]([^\\/\s\"'`,;)\]]*)")
# What makes the segment documentation rather than a leak. Spelled out rather than
# guessed at with a wildcard, because the whole point of the rule is that a name it does
# not recognise is reported: a placeholder convention nobody declared is a real name to
# everybody reading it.
PLACEHOLDER_SEGMENT = re.compile(
    r"(?i)^(?:\{[^{}]*\}|<[^<>]*>|%[a-z_][a-z0-9_]*%|\$\{?[a-z_][a-z0-9_]*\}?"
    r"|username|user|you|nome|usuario|seu-usuario)$"
)


def publishable_terms(
    tools: list[dict], marker: dict, engine: dict | None
) -> tuple[list[Forbidden], str | None]:
    """Everything that must not travel, and the note explaining the one exception.

    THE DERIVED LIST is the point. A hand-written list of forbidden
    names goes stale the first time an entry is added and nothing reports that it has -
    the same lesson `check_scope_leak` learned when its hand-listed directories missed
    197 tracked files. So the `own` names are read off the catalogue on every run, which
    also reaches the two entries whose `repo` is a private remote URL and which therefore
    never become a scan root at all.
    """
    here = data_root()
    out: list[Forbidden] = [
        Forbidden(n, f"the bank's name ({MARKER} [invariant].needles)")
        for n in declared_needles(marker)
    ]
    out.append(Forbidden(here.as_posix(), "the bank's absolute path"))
    if str(here) != here.as_posix():
        # Windows writes the same path both ways and a leak takes whichever spelling the
        # tool that wrote it prefers.
        out.append(Forbidden(str(here), "the bank's absolute path, in the native spelling"))
    for term, _placeholder in scoped_terms(tools):
        out.append(
            Forbidden(
                term,
                "the name of a machine-local tool (tools.local/ - a name that must not travel)",
            )
        )
    exempt_file = str(engine.get("_file")) if engine else None
    for t in tools:
        if t.get("ring") != "own" or (exempt_file and str(t.get("_file")) == exempt_file):
            continue
        name = str(t.get("name") or "").strip()
        if name:
            out.append(Forbidden(name, f"the name of an `own` catalogue entry ({t.get('_file')})"))
    for extra in (marker.get("publish") or {}).get("forbid_extra") or []:
        if isinstance(extra, str) and extra.strip():
            out.append(Forbidden(extra.strip(), f"{MARKER} [publish].forbid_extra"))

    # DE-DUPLICATED, first reason wins. A machine-local entry at ring `own` is reached by
    # two of the branches above - it is a scoped name AND an `own` name - and the same
    # string forbidden twice prints the same leak as two findings, which inflates the
    # count the note ends on. First wins because the branches are ordered by how much the
    # reason says: "the name cannot travel at all" outranks "it names an entry".
    seen: set[str] = set()
    deduped: list[Forbidden] = []
    for f in out:
        key = f.term.lower()
        if key not in seen:
            seen.add(key)
            deduped.append(f)
    out = deduped

    note = None
    if engine:
        note = (
            f"[NOTE] publication check: {str(engine.get('name'))!r} ({engine.get('_file')}) is "
            "the engine's own entry - its `repo` resolves to the declared "
            "framework_worktree - so its name is not a forbidden term. Without the "
            "exception the check would be red forever on the engine's own pyproject.toml "
            "and banners. It matches by IDENTITY, on `repo`, which is why "
            'the entry stays `visibility = "private"`: a public entry needs `repo` in URL '
            "form and a URL is not a checkout."
        )
    return out, note


def _user_literal_hits(text: str) -> list[str]:
    """The `C:/Users/<name>` literals in `text` that name an account rather than a slot."""
    hits = []
    for m in USER_LITERAL.finditer(text):
        segment = m.group(1)
        if segment and not PLACEHOLDER_SEGMENT.match(segment):
            hits.append(m.group(0))
    return sorted(set(hits))


def check_publishable(
    tools: list[dict], marker: dict, claude_home: str | None = None
) -> tuple[list[str], str | None]:
    """Is the engine's checkout fit to be published? Returns (FAILs, why-it-did-not-run).

    ONLY THE PRIVATE REPOSITORY CAN ASK THIS. The list of what must not
    travel - this repo's name and path, the machine-local tool names, the derived list of
    every `own` entry, the operator's own extras - exists here and nowhere else, so the
    engine's CI is left checking FORMS (no real `C:/Users/`, no `Path.home()` outside
    bootstrap) and never names. That asymmetry is not a gap to close later; a public CI
    that could check the names would have to hold them.

    A SECOND RETURN VALUE rather than an empty list, for the reason every check in this
    file eventually needed one: "0 findings" out of a check that read nothing is the exact
    wording of a pass. The key is undeclared until the engine's repository exists,
    it names a checkout CI will never have, and `--no-data-plane` rehearses that - three
    ways not to run, each of which has to print as a non-run rather than as a green line.
    """
    declared = str((marker.get("publish") or {}).get("framework_worktree") or "").strip()
    if not declared:
        return [], (
            f"`[publish].framework_worktree` is not declared in {MARKER}, so no engine "
            "checkout was read. Nothing was compared against the list of what must not "
            "travel, which is not the same as comparing it and finding nothing"
        )
    # The same profiles main() identified the engine with. Passed rather than recomputed
    # so the two readings of "which entry is the engine" cannot answer differently on a
    # profile whose `repo` happens to be written with `{claude_home}`.
    profiles = profile_paths(claude_home)
    tried: list[str] = []
    root: Path | None = None
    for paths in profiles:
        cand = Path(expand(declared, paths))
        tried.append(cand.as_posix())
        if cand.is_dir():
            root = cand
            break
    if root is None:
        return [], (
            f"`[publish].framework_worktree` is {declared!r}, which is not a directory in "
            f"any profile (tried {', '.join(sorted(set(tried)))}). The checkout is absent "
            "here - on a runner it always is - so the list of what must not travel was "
            "compared against nothing"
        )

    found = git_ls_files(root)
    if found is None:
        return [
            f"[FAIL] publication check: the engine checkout could not be enumerated - "
            f"`git ls-files` failed in {root}. git is absent, or the directory is not a "
            "checkout; either way nothing was read, which is not the same as reading it "
            "clean"
        ], None

    engine = framework_entry(tools, declared, profiles)
    terms, exception_note = publishable_terms(tools, marker, engine)
    if exception_note:
        print(exception_note)
    lowered = [(f, f.term.lower()) for f in terms]
    fails: list[str] = []
    for p in found:
        rel = p.relative_to(root).as_posix()
        for f, low in lowered:
            if low in rel.lower():
                fails.append(
                    f"[FAIL] publication check: FILENAME carries {f.term!r} - {f.why}: {rel}"
                )
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        low_text = text.lower()
        for f, low in lowered:
            if low in low_text:
                fails.append(f"[FAIL] publication check: {rel} carries {f.term!r} - {f.why}")
        if p.name.lower().endswith(".py") and p.name != PATH_HOME_EXEMPT and PATH_HOME.search(text):
            fails.append(
                f"[FAIL] publication check: {rel} calls `Path.home()` - only "
                f"`{PATH_HOME_EXEMPT}` may, because finding the home directory is its job. "
                "Everywhere else the machine path belongs to the profile's [paths]"
            )
        for literal in _user_literal_hits(text):
            fails.append(
                f"[FAIL] publication check: {rel} carries the literal {literal!r} - a real "
                "account name in a published tree. Write the placeholder the profile fills "
                "(`C:/Users/{user}/...`) or read the path from [paths]"
            )
    print(
        f"[NOTE] publication check: {len(found)} file(s) in {root} read against "
        f"{len(terms)} forbidden term(s) and the two form rules - {len(fails)} finding(s)"
    )
    return fails, None


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="The backing gate: schema, evidence, licences, staleness, version "
        "sites, and the invisible-hand invariant."
    )
    ap.add_argument(
        "--data-root",
        help="the radar data root, instead of searching upwards for radar.toml. For CI "
        "and nothing else; it still has to name a directory that carries the "
        "marker",
    )
    ap.add_argument(
        "--claude-home",
        help="override every profile's [paths].claude_home (the profiles carry example "
        "paths, so a fresh clone needs this to point at the real one)",
    )
    ap.add_argument(
        "--no-data-plane",
        action="store_true",
        help="answer as a CI runner does: no claude_home, no governed worktree checked "
        "out, no gitignored tools.local/. A rehearsal of the run CI will make - on a "
        "developer machine every one of those is present, so --claude-home alone does "
        "NOT reproduce it",
    )
    ap.add_argument(
        "--fast",
        action="store_true",
        help=f"cap the invariant scan at {INVARIANT_MAX_FILES} files per root, as it was "
        "capped by default until the ceiling was measured at 34%% coverage. Every root it "
        "cuts prints TRUNCADO: a fast run proves less than the default complete one, and "
        "has to say so where the number is read",
    )
    return ap


def main() -> None:
    args = build_parser().parse_args()
    # First, and before anything reads a catalogue: without a data root there is nothing
    # to judge, and that leaves through exit 2 rather than the 1 a finding costs.
    begin_command(args.data_root)
    marker = load_marker()
    # tools.local/ is gitignored, so a CI checkout does not have it. Dropping it under
    # --no-data-plane is what makes the rehearsal faithful rather than approximate.
    tools = load_tools(include_local=not args.no_data_plane)
    snap_name, snap = latest_snapshot()
    today = datetime.date.today()
    fails = warns = flags = 0
    no_exit: list[str] = []
    no_telemetry: list[str] = []
    # validate() can only check the SHAPE of `environments`; only here is the set of real
    # profile names known. A tool scoped to an environment that does not exist is silently
    # invisible everywhere — the worst failure mode, because nothing reports it.
    known_envs = {e.get("name") for e in load_environments()}
    local_entries = [t.get("name", t["_file"]) for t in tools if t.get("_local")]
    for t in tools:
        name = t.get("name", t["_file"])
        for e in validate(t):
            print(f"[FAIL] {name}: {e}")
            fails += 1
        stray = sorted(set(t.get("environments") or []) - known_envs)
        if stray:
            print(
                f"[FAIL] {name}: scoped to unknown environment(s) {stray} - "
                "the tool would be invisible in every profile"
            )
            fails += 1
        hist = t.get("history") or [{}]
        if t.get("ring") in ("adopt", "pilot") and not hist[-1].get("evidence"):
            print(f"[WARN] {name}: {t['ring']} transition carries no evidence link")
            warns += 1
        if missing_pilot_exit(t):
            no_exit.append(name)
        if missing_telemetry(t):
            no_telemetry.append(name)
        # A mistyped key in [telemetry] is invisible in TOML and produces a report full
        # of zeros that reads like evidence of disuse. WARN rather than FAIL: the set of
        # known keys grows, and a stray key breaks nothing except the measurement it was
        # meant to configure.
        stray_keys = unknown_telemetry_keys(t)
        if stray_keys:
            print(
                f"[WARN] {name}: [telemetry] has unknown key(s) {stray_keys} - nothing "
                "reads them, so a mistyped matcher name measures nothing and reports a "
                f"zero. Known keys: {', '.join(TELEMETRY_KEYS)}"
            )
            warns += 1
        absent = str(t.get("telemetry_absent") or "").strip()
        if absent and t.get("ring") in ("own", "adopt"):
            # Printed every run, on purpose. A declared gap that goes quiet is just an
            # undeclared gap with extra steps.
            print(f"[NOTE] {name}: telemetry deliberately absent - {absent}")
        lic = t.get("license", "")
        if any(k in lic for k in COPYLEFT):
            print(f"[NOTE] {name}: copyleft license ({lic})")
        elif any(k in lic for k in SOURCE_AVAILABLE):
            print(f"[NOTE] {name}: source-available/noncommercial license ({lic})")
        m = snap.get(name)
        if not m:
            continue
        if m.get("archived"):
            # A dead upstream is a FAILURE only where the entry claims you use it. At
            # `discard` an archived repo is the judgement being confirmed, and at
            # `observe` it is a watchlist item resolving itself — reporting either as a
            # gate failure trains the reader to ignore the word FAIL.
            if t.get("ring") in ("own", "adopt", "pilot"):
                print(f"[FAIL] {name}: repo archived at ring {t['ring']}")
                fails += 1
            else:
                print(f"[NOTE] {name}: repo archived (already {t.get('ring')})")
        pushed = m.get("pushed")
        if pushed and t.get("ring") in ("adopt", "pilot"):
            age = (today - datetime.date.fromisoformat(pushed)).days
            if age > STALE_DAYS:
                print(f"[WARN] {name}: last push {age}d ago at ring {t['ring']}")
                warns += 1
        registry, stars, dl = t.get("registry"), m.get("stars"), m.get("downloads_month")
        # Unknown downloads on a high-star entry is NOT a pass. The astroturf test needs
        # downloads to fire, so a failed registry lookup silently exonerates exactly the
        # entries the test exists for — observed when a refresh dropped 20 download
        # figures and the flag count fell from 4 to 1 while nothing improved. Absence of
        # the number is reported as absence, never as health.
        # Gated on a DECLARED registry: an entry with no registry has no download figure
        # by nature (distributed only through git), and flagging those buried the real
        # signal under 28 false positives on the first run. The flag means "a number was
        # expected and is missing", not "no number exists".
        if registry and stars and stars > ASTROTURF_MIN_STARS and dl is None:
            print(
                f"[FLAG] {name}: {stars} stars and NO download figure - the astroturf "
                "test cannot run; treat as unverified, not as clean"
            )
            flags += 1
        elif not registry and dl is not None:
            # A figure in the snapshot with no `registry` behind it any more: the
            # entry's own tools/*.toml stopped naming a source for it (removed, or
            # changed to something snapshot.py has not re-measured under). Observed on a
            # catalogue entry whose wrong `registry` was corrected away after a figure
            # had already been measured under it: the snapshot's carry-forward kept the
            # figure the wrong registry had produced, because the carry-forward did not
            # check whether the registry it was measured under still applies. The gate
            # has no figure it can trust here, so it neither flags nor stays silent -
            # same discipline as every other check this file cannot run: it says so.
            print(
                f"[NOTE] {name}: {dl}/mo downloads in the snapshot but no `registry` is "
                "declared - the astroturf test cannot run; the figure is not evidence "
                "about this entry"
            )
        elif m.get("downloads_stale"):
            print(
                f"[NOTE] {name}: download figure is carried forward from "
                f"{m.get('downloads_as_of')} (registry lookup failed since)"
            )
        if (
            registry
            and stars
            and dl is not None
            and stars > ASTROTURF_MIN_STARS
            and dl < stars * ASTROTURF_RATIO
        ):
            print(f"[FLAG] {name}: astroturf signature - {dl}/mo downloads vs {stars} stars")
            flags += 1
    if no_exit:
        # Now a FAIL, not a backlog. This started as a WARN because the rule postdated 31
        # pilot entries and a gate that cannot be passed stops being read. The 2026-07-26
        # de-duplication cleared the backlog to zero by demoting every pilot that was
        # never actually installed, so the condition that justified the leniency is gone.
        # A pilot is an experiment; refusing to state in advance what would end it is how
        # 31 of them accumulated with one measured. Cheap to satisfy, so it blocks.
        fails += len(no_exit)
        for name in sorted(no_exit):
            print(
                f"[FAIL] {name}: ring=pilot with no [pilot_exit] - state what would adopt "
                "or decline it before the field data exists, or set ring=observe"
            )

    if no_telemetry:
        # A FAIL from the day it lands, and that is only defensible because the backlog
        # was cleared in the same pass. The pilot_exit rule earned its severity the hard
        # way - it sat as a WARN nobody read while 31 pilots accumulated - so this one
        # skips the WARN phase rather than repeating the lesson.
        #
        # It is not a demand for a matcher on everything: MEASURABLE_ARTIFACTS excludes
        # the kinds a transcript cannot see, because forcing a matcher there produces
        # one that cannot match, and a zero that cannot mean anything is worse than an
        # honest gap.
        fails += len(no_telemetry)
        for name in sorted(no_telemetry):
            print(
                f"[FAIL] {name}: ring={'own/adopt'} with no [telemetry] matcher - a ring "
                "claims observed state, so declare how the claim is checked (match / "
                "match_skill / match_command), or document the absence if the artifact "
                "leaves no tool call"
            )

    # A repo-only check, and one the estate standard asks for by name: the
    # version sites agree, by something that fails rather than by convention. Shares its
    # one implementation with tests/test_version_sites.py.
    for err in version_site_errors():
        print(f"[FAIL] version sites: {err}")
        fails += 1

    if local_entries:
        print(
            f"[NOTE] {len(local_entries)} machine-local entr"
            f"{'y' if len(local_entries) == 1 else 'ies'} loaded from tools.local/ "
            f"({', '.join(sorted(local_entries))}) - gated by the gate, excluded from "
            "every tracked artefact by render.py"
        )

    # ------------------------------------------------------------ machine-dependent half
    #
    # Named, never silently skipped. Each of these needs something that lives on a
    # machine, and on a CI runner none of it is there: the profiles' claude_home does not
    # exist, no governed worktree is checked out, and tools.local/ is gitignored so it is
    # not in the checkout at all. Reporting that as a pass is how a gate becomes
    # decoration - which is precisely the failure an unverified invariant
    # decays into.
    roots: list[Root] = []
    unresolved: list[str] = []
    if not args.no_data_plane:
        roots, unresolved = data_plane_roots(tools, args.claude_home)
    skipped: list[str] = []

    # The needles, as data. An empty list is legal and WEAKER, and the
    # gate says which of the two it is scanning with, because both print "holds".
    names = declared_needles(marker)
    if not names:
        print(
            f"[NOTE] invariant: {MARKER} declares no [invariant].needles, so the "
            "scan runs on only the data root's absolute path. A path is the strong half of "
            "the needle and a name is the half that survives a move, so this catches a "
            "file that hard-codes the location and misses every file that refers to the "
            "catalogue by name"
        )

    # The engine's name, for the three execution files and for nothing else.
    # Identified by `repo`, never by a second key that could disagree.
    declared_worktree = str((marker.get("publish") or {}).get("framework_worktree") or "").strip()
    engine = (
        framework_entry(tools, declared_worktree, profile_paths(args.claude_home))
        if declared_worktree
        else None
    )
    framework_name = str(engine.get("name")) if engine else None
    if framework_name:
        print(
            f"[NOTE] invariant: {framework_name!r} is a needle in settings.json, any "
            "hooks/hooks.json and CLAUDE.md and nowhere else - naming the "
            "engine in the data plane is ordinary, binding a hook to it is what would make "
            "a session depend on it"
        )
    else:
        why = (
            f"`[publish].framework_worktree` is not declared in {MARKER}"
            if not declared_worktree
            else f"no `own` entry's `repo` resolves to {declared_worktree!r}, so the engine "
            "has no name in this catalogue yet (writing that entry gives it one)"
        )
        print(
            "[NOTE] invariant: the engine's name is NOT a needle in settings.json, "
            f"hooks/hooks.json or CLAUDE.md - {why}"
        )

    if args.no_data_plane:
        pub_skip: str | None = (
            "--no-data-plane was asked for, so the engine checkout was not read even "
            "where one exists"
        )
        pub_fails: list[str] = []
    else:
        pub_fails, pub_skip = check_publishable(tools, marker, args.claude_home)
    for msg in pub_fails:
        print(msg)
        fails += 1

    if local_entries:
        for msg in check_scope_leak(tools):
            print(msg)
            fails += 1
    else:
        because = (
            "--no-data-plane was asked for, so tools.local/ was not loaded"
            if args.no_data_plane
            else "this checkout has none (the directory is gitignored)"
        )
        skipped.append(
            "the scope-leak scan over tracked files - it searches for the NAMES declared "
            f"by tools.local/ entries; {because}. With no scoped name to look for it "
            "cannot fail, so a green run says nothing about leaks"
        )

    if roots:
        if unresolved:
            # Printed beside the roots, never instead of them. An `own` entry that does
            # not resolve to a tree is the failure mode this scan had for months, and it
            # is invisible unless the absence is named: a root that simply does not exist
            # looks the same as one that was scanned clean.
            print(
                f"[NOTE] invariant: {len(unresolved)} `own` entr"
                f"{'y' if len(unresolved) == 1 else 'ies'} did not become a data-plane root:"
            )
            for u in unresolved:
                print(f"[NOTE]   not a root: {u}")
        exempt_map, exempt_notes = declared_exempt(marker)
        for note in exempt_notes:
            print(note)
        for msg in check_invariant(roots, names, framework_name, fast=args.fast, exempt=exempt_map):
            print(msg)
            fails += 1
    elif args.no_data_plane:
        skipped.append(
            "the invariant scan - --no-data-plane was asked for, so the data "
            "plane was not looked at even where one exists"
        )
    else:
        homes = ", ".join(str(h) for h in claude_homes(args.claude_home)) or "(none declared)"
        skipped.append(
            f"the invariant scan - no data plane is reachable from here: no "
            f"claude_home exists at {homes}, and no governed tool worktree is checked out"
        )

    if skipped:
        why = (
            "deliberately ignored (--no-data-plane)"
            if args.no_data_plane
            else "not reachable from here"
        )
        print(
            f"\n[NOTE] the data plane is {why} - {len(skipped)} check(s) did NOT run. "
            "Named rather than counted, because a scan of nothing prints the same "
            "'0 references' a real one does:"
        )
        for s in skipped:
            print(f"[NOTE]   skipped: {s}")
        print(
            "[NOTE]   not applicable: machine probes - this gate runs none. Installation "
            "probes belong to apply.py, version probes to versions.py, and neither is "
            "reached from here."
        )

    # Reported on its own line rather than folded into the list above, because it is a
    # different absence: the publication check is not missing a DATA PLANE, it is missing
    # the engine's checkout, and on a day when one is declared and present it runs with
    # no data plane at all.
    if pub_skip:
        print(
            f"\n[NOTE] the publication check did NOT run - {pub_skip}. This is not a pass: "
            "only this repository holds the list of what must not travel, "
            "so a run that skips it has checked nothing about publication"
        )

    print(
        f"\n{len(tools)} tools checked against snapshot {snap_name or '(none)'}"
        f" - {fails} FAIL, {warns} WARN, {flags} astroturf FLAG"
        + (f", {len(skipped)} check(s) skipped without a data plane" if skipped else "")
        + (", publication check NOT run" if pub_skip else "")
    )
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
