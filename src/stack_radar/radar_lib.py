"""Shared loading/validation for stack-radar. Stdlib only (Python 3.11+).

THE INVISIBLE-HAND INVARIANT
----------------------------
This repository is a CONTROL PLANE. The stack it governs (plugins, skills, MCP
servers, settings, CLAUDE.md) is the DATA PLANE and must never reference the radar
or depend on it at runtime. Knowledge flows one way: the radar reads and writes the
stack; the stack never reads the radar. If this repo vanishes, the stack freezes in
its current state and keeps working. `gate.py` enforces this mechanically.
"""

from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path, PurePath

from .paths import (
    MARKER,
    DataRootNotFound,
    data_root,
    env_dir,
    resolve_data_root,
    snap_dir,
    tools_dir,
    tools_local_dir,
)


class FrameworkMismatch(Exception):
    """The engine reading this catalogue is outside the range the catalogue requires."""


# ------------------------------------------------------------------------ the marker


def load_marker(root: Path | None = None) -> dict:
    """The `radar.toml` at the root, as a dict."""
    with ((root or data_root()) / MARKER).open("rb") as fh:
        return tomllib.load(fh)


# One comparator of a `requires_framework` range: `>=0.1.0`, `<0.3.0`, `==0.2.0`. A range
# is those, comma-separated, and every one of them has to hold. Deliberately smaller than
# PEP 440: this is a stdlib-only module, and the versions it compares are three dotted
# integers - the `X.Y.Z` of every release heading - so the parser that reads them can
# be too - and it REFUSES what it does not understand rather than passing it.
_COMPARATOR = re.compile(r"^(>=|<=|==|!=|>|<)\s*(\d+(?:\.\d+)*)$")

_COMPARE = {
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    ">": lambda a, b: a > b,
    "<": lambda a, b: a < b,
}


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in str(version).split("."))


def framework_errors(version: str, requires: str) -> list[str]:
    """Why `version` does not satisfy the range. Empty list == it does."""
    errs: list[str] = []
    try:
        have = _version_key(version)
    except ValueError:
        return [f"{version!r} is not a dotted numeric version, so nothing can be compared to it"]
    for clause in str(requires).split(","):
        clause = clause.strip()
        if not clause:
            continue
        m = _COMPARATOR.match(clause)
        if not m:
            errs.append(
                f"`requires_framework` clause {clause!r} is not a comparator followed by a "
                f"dotted version ({'/'.join(_COMPARE)}) - an unreadable range is refused "
                "rather than ignored, because an ignored range constrains nothing"
            )
            continue
        op, want = m.group(1), m.group(2)
        if not _COMPARE[op](have, _version_key(want)):
            errs.append(f"engine {version} does not satisfy {clause}")
    return errs


def framework_version(root: Path | None = None) -> str:
    """The version of the engine reading this catalogue: the engine's own `__version__`.

    It used to be the DATA ROOT'S `pyproject.toml`. That was a placeholder from the time
    the engine lived inside the catalogue it read, when the two could hardly disagree. An
    installed engine has a version of its own, and a catalogue's `requires_framework`
    constrains that one; read from the catalogue, the check compared a catalogue with
    itself and could not fail for a real reason. `root` stays in the signature so every
    caller keeps working; it no longer changes the answer.
    """
    from . import __version__

    return __version__


def check_framework(root: Path | None = None, marker: dict | None = None) -> None:
    """Raise unless the engine satisfies the catalogue's `requires_framework`.

    Checked on EVERY command, with two exceptions: `changelog-gate` and
    `check-version-sites` judge whatever repository they are run from, run without a
    marker at all, and so have no range to be checked against - which is how the engine's
    own CI runs them in a repository that has no `radar.toml`.

    It fails naming BOTH versions. An engine too old for a catalogue does not misbehave
    loudly: it reads the fields it knows and silently ignores the rest, so the report is
    plausible and wrong. The error has to say what was required and what is present, or
    the reader is left diffing two repositories to find out.
    """
    root = root or data_root()
    requires = (marker if marker is not None else load_marker(root)).get("requires_framework")
    if not requires:
        return
    version = framework_version(root)
    errs = framework_errors(version, str(requires))
    if errs:
        raise FrameworkMismatch(
            f"this catalogue requires framework {requires!r} and the engine here is "
            f"{version}: " + "; ".join(errs)
        )


def begin_command(override: str | None = None, *, stream=None) -> Path:
    """Every command's first act: resolve the root, check the range, say both.

    ON STDERR, and that is the one deliberate reading of "prints the resolved root on the
    first line". Two commands write a machine-readable document to stdout -
    `field_report.py --json` emits the report another reader parses, and
    `render_feedback_targets.py --stdout` emits the artefact itself - so a banner there
    would corrupt them. stderr is still the first line the command prints, and it stays
    out of every pipe that means something.

    Both failures leave through EXIT CODE 2. The gate spends 1 on findings, so a run that
    never found its catalogue has to be distinguishable from one that judged a catalogue
    and did not like it.
    """
    try:
        root = resolve_data_root(override)
        check_framework(root.path)
    except (DataRootNotFound, FrameworkMismatch) as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    print(
        f"[NOTE] data root {root.path.as_posix()} (found by {root.source})",
        file=stream or sys.stderr,
    )
    return root.path


RINGS = ["own", "adopt", "pilot", "observe", "discard"]
AXES = [
    "cc-ecosystem",
    "token-economy",
    "codebase-understanding",
    "orchestration",
    "eval-observability",
    "memory-knowledge",
    "python-engineering",
    "data-lineage",
    "mcp-servers",
    "multi-model",
]
REQUIRED = ("name", "repo", "axis", "ring", "license", "visibility", "note", "artifact")

# The one admitted value of `repo` for a public entry with no repository at all.
# render.py writes `repo` as a Markdown link in the public projection; a local path
# there would publish that path, and this sentinel is how a real phantom entry (a tool
# that two research models both invented) is recorded honestly instead of by lying
# about a URL or hiding the tool from the public projection.
NO_REPO_SENTINEL = "(repo does not exist)"
_REPO_URL_PREFIXES = ("http://", "https://", "git@")

# WHAT KIND OF THING IS THIS — the dimension `axis` does not carry.
# `axis` says which problem a tool addresses; `artifact` says what you actually install
# and where it runs, which is what decides the three things an axis cannot answer:
# whether it costs context, how big its blast radius is, and whether a locked-down
# corporate environment can have it at all. Two entries on the same axis can be a
# library and a hosted service — utterly different commitments.
ARTIFACTS = [
    "cc-plugin",  # Claude Code plugin: skills / agents / commands / hooks
    "mcp-server",  # exposes a tool surface INTO the agent's context (costs tokens)
    "cc-lsp",  # Claude Code language-server plugin: native, no tool surface
    "cli",  # standalone command, run from a shell or CI; no context cost
    "python-lib",  # imported by project code, so it becomes a runtime dependency
    "service",  # a server + UI that somebody has to operate, authenticate and patch
    "standard",  # spec or wire format; nothing to install, only something to target
    "corpus",  # authored knowledge, not executable
    "unknown",  # never established — reserved for entries the backing gate rejected
    # before their nature was worth verifying (astroturf, phantoms). Honest by design:
    # "we did not check what this is, because it failed on evidence first."
]

# [version].policy — what "current enough" means for one entry. The radar tracked WHETHER
# a tool was present and never WHICH version, so "installed" and "current" were one word.
#   latest  warn when behind the registry (default for anything with a `registry`)
#   pin     fail on any deviation — for a tool whose behaviour was MEASURED at one version
#   floor   fail below `min`, silent above — a required fix, newer is fine
#   any     never compared — the version is not a meaningful axis, or it is pinned
#           per-project by each repo's lockfile rather than machine-wide
VERSION_POLICIES = ["latest", "pin", "floor", "any"]

# [install].kind — how a tool is converged into an environment by apply.py
INSTALL_KINDS = [
    "uv-tool",  # uv tool install <spec>
    "npm-tool",  # npm install -g <pkg> (how the tool actually ships)
    "claude-plugin",  # a Claude Code plugin dir/marketplace entry
    "mcp-plugin",  # a plugin whose payload is an MCP server
    "mcp-server",  # claude mcp add ...
    "pip",  # pip/uv pip install into a project
    "repo",  # a working tree the user maintains (verify presence only)
    "manual",  # apply prints instructions, never executes
    "none",  # nothing to install (a standard, a spec, a dead entry)
]

# [install].check / .apply are single commands, not shell lines: apply.py splits them
# with shlex and runs them without a shell, so pipes/redirects/&& are not available and
# paths must use forward slashes. `{key}` placeholders are filled from the environment
# profile's [paths] table, which is what keeps one entry usable on several machines.


# --------------------------------------------------------------------------- load


def load_tools(include_local: bool = True, root: Path | None = None) -> list[dict]:
    """Every tool entry. Local (gitignored) entries are tagged `_local = True`.

    Pass include_local=False when producing anything that gets committed or published.
    render.py does exactly that; see tools_local_dir() for why it matters.
    """
    root = root or data_root()
    tools = []
    for f in sorted(tools_dir(root).glob("*.toml")):
        with f.open("rb") as fh:
            t = tomllib.load(fh)
        t["_file"] = f.name
        t["_local"] = False
        tools.append(t)
    local = tools_local_dir(root)
    if include_local and local.exists():
        for f in sorted(local.glob("*.toml")):
            with f.open("rb") as fh:
                t = tomllib.load(fh)
            t["_file"] = f"tools.local/{f.name}"
            t["_local"] = True
            tools.append(t)
    return tools


def expand_home(paths: dict | None, home: PurePath) -> dict:
    """`[paths]` with a leading `~` written as the home directory, forward-slashed.

    A profile is tracked, and a path spelled out in it carries the account name into the
    catalogue; `~/.claude` says the same thing without naming anybody. Every reader of a
    profile expands it here, so a consumer never sees the `~`. Forward slashes because
    that is how profiles spell their paths, so a spelled-out path and a `~` one load
    equal. Only `~` alone or followed by a separator counts: `~other/x` is another
    account's home, and guessing where that is would be wrong more often than not.

    Handed the home rather than finding it, like `redact.redact_home`: only
    `bootstrap.home_dir()` looks the home up.
    """
    out = dict(paths or {})
    for key, value in out.items():
        if isinstance(value, str) and (value == "~" or value[:2] in ("~/", "~\\")):
            out[key] = home.as_posix() + ("/" + value[2:] if len(value) > 2 else "")
    return out


def with_home(profile: dict, home: PurePath | None = None) -> dict:
    """`profile` with its `[paths]` expanded (`expand_home`). The home is asked of
    `bootstrap.home_dir()` when not handed; imported here because bootstrap imports
    this module."""
    if not isinstance(profile.get("paths"), dict):
        return profile
    if home is None:
        from .bootstrap import home_dir

        home = home_dir()
    return {**profile, "paths": expand_home(profile["paths"], home)}


def load_environments(root: Path | None = None) -> list[dict]:
    """Every committed environment profile, `[paths]` expanded (`expand_home`).

    Machine-local overlays (`*.local.toml`) are deliberately excluded: they are
    fragments, not profiles, and because "corp.local.toml" sorts before "corp.toml" a
    naive glob makes load_environment("corp") return the fragment as if it were the
    whole profile. Overlays are layered only through the explicit resolver in apply.py.
    """
    envs = []
    directory = env_dir(root)
    if not directory.exists():
        return envs
    for f in sorted(directory.glob("*.toml")):
        if f.name.endswith(".local.toml"):
            continue
        with f.open("rb") as fh:
            e = tomllib.load(fh)
        e["_file"] = f.name
        envs.append(with_home(e))
    return envs


def load_environment(name: str, root: Path | None = None) -> dict | None:
    for e in load_environments(root):
        if e.get("name") == name:
            return e
    return None


def default_environment(root: Path | None = None) -> str:
    """The environment name `--env` falls back to when a script's flag is not given.

    A property of the catalogue (`radar.toml [default_environment]`), not of the code
    that reads it - a bank that renames its environments only has to touch the
    marker. `apply.py --apply` is deliberately not one of the readers: it writes, and
    refuses an implicit environment rather than default to one.
    """
    return str(load_marker(root).get("default_environment") or "personal")


def latest_snapshot(root: Path | None = None) -> tuple[str | None, dict]:
    snaps = sorted(snap_dir(root).glob("*.json"))
    if not snaps:
        return None, {}
    with snaps[-1].open(encoding="utf-8") as fh:
        return snaps[-1].name, json.load(fh)


# ----------------------------------------------------------------------- validate


def validate(t: dict) -> list[str]:
    """Schema errors for one tool entry. Empty list == valid."""
    errs = []
    for k in REQUIRED:
        if not t.get(k):
            errs.append(f"missing field: {k}")
    if t.get("ring") not in RINGS:
        errs.append(f"bad ring: {t.get('ring')}")
    if t.get("axis") not in AXES:
        errs.append(f"bad axis: {t.get('axis')}")
    if t.get("artifact") not in ARTIFACTS:
        errs.append(f"bad artifact: {t.get('artifact')}")
    # An `unknown` artifact is only defensible where the entry never got far enough to
    # matter. Claiming it for something you are running is a gap pretending to be a value.
    if t.get("artifact") == "unknown" and t.get("ring") not in ("discard", "observe"):
        errs.append("artifact=unknown is only for discard/observe entries")
    if t.get("visibility") not in ("public", "private"):
        errs.append(f"bad visibility: {t.get('visibility')}")
    # A public entry's `repo` is rendered as a link (render.py), so a local path there
    # would publish that path. A URL is the honest case; the sentinel is the honest
    # alternative for a tool that never had a repository to begin with - not silence,
    # since a phantom entry the projection dropped would look like it was never caught.
    if t.get("visibility") == "public":
        repo = str(t.get("repo") or "")
        if repo != NO_REPO_SENTINEL and not repo.startswith(_REPO_URL_PREFIXES):
            errs.append(
                f"visibility=public needs `repo` as a URL ({'/'.join(_REPO_URL_PREFIXES)}) "
                f"or the literal {NO_REPO_SENTINEL!r} sentinel for a tool with no "
                "repository - render.py writes `repo` as a link in the public projection"
            )

    # TWO SCOPES, deliberately separate — they answer different questions:
    #   top-level `environments`   which environments this tool EXISTS in at all
    #   [install].environments     where apply.py should CONVERGE it
    # serena may exist everywhere and be installed only on the personal machine; an
    # in-house corporate library exists only in `corp`. Collapsing them would force one
    # to lie. The subset rule below keeps them from drifting apart.
    envs = t.get("environments")
    if envs is not None:
        if not isinstance(envs, list) or not all(isinstance(s, str) for s in envs):
            errs.append("`environments` must be a list of environment names")
        elif not envs:
            errs.append("`environments` is empty - omit it to mean 'every environment'")
        # An environment-scoped entry is in-house or site-specific by construction, so it
        # has no business in the published projection. Enforced rather than trusted: the
        # public render filters on visibility, and one `public` here would leak a name.
        elif t.get("visibility") != "private":
            errs.append(
                "an environment-scoped tool must be visibility=private "
                "(scoping exists to keep site-specific names out of the projection)"
            )
        inst_envs = (t.get("install") or {}).get("environments")
        if isinstance(envs, list) and isinstance(inst_envs, list):
            stray = sorted(set(inst_envs) - set(envs))
            if stray:
                errs.append(
                    f"[install].environments names {stray} outside `environments` - "
                    "cannot converge a tool where it does not exist"
                )

    hist = t.get("history") or []
    if not hist:
        errs.append("empty history")
    elif hist[-1].get("ring") != t.get("ring"):
        errs.append(f"ring {t.get('ring')!r} != last history ring {hist[-1].get('ring')!r}")

    es = t.get("eval_status", "unmeasured")
    if es not in ("unmeasured", "eval-pending") and not es.startswith("measured:"):
        errs.append(f"bad eval_status: {es}")

    inst = t.get("install")
    if inst is not None:
        kind = inst.get("kind")
        if kind not in INSTALL_KINDS:
            errs.append(f"bad install.kind: {kind}")
        if kind not in ("none", "manual") and not inst.get("check"):
            errs.append(
                f"install.kind={kind} needs a `check` probe (idempotent, exit 0 == present)"
            )
        if kind not in ("none", "manual", "repo") and not inst.get("apply"):
            errs.append(f"install.kind={kind} needs `apply` (or use kind=manual)")
        # A manual entry whose instruction is missing tells the operator nothing, and
        # manual is the escape hatch precisely for the cases a script must not guess.
        if kind == "manual" and not inst.get("instruction"):
            errs.append("install.kind=manual needs `instruction` (what to run by hand)")
        envs = inst.get("environments")
        if envs is not None and (
            not isinstance(envs, list) or not all(isinstance(s, str) for s in envs)
        ):
            errs.append("[install].environments must be a list of environment names")
        # `remove` is advice printed when a machine still carries an unwanted tool.
        # Nothing executes it: apply proposes removals and never performs them.
        if inst.get("remove") is not None and not isinstance(inst["remove"], str):
            errs.append("[install].remove must be a string (a suggestion, not a script)")

    fb = t.get("feedback")
    if fb is not None:
        # ring=own, because a feedback loop needs a maintainer who will act on the report.
        # `own` means authored — by you or by your team — which is exactly the case for an
        # in-house corporate library: you dogfood it and you can land the fix. A
        # third-party tool gets an upstream issue, not a private report nobody reads.
        if t.get("ring") != "own":
            errs.append(
                "[feedback] is only for ring=own tools (authored by you or your team - "
                "they are the ones you dogfood and can actually fix)"
            )
        if not fb.get("dir"):
            errs.append("[feedback] needs `dir` (relative to the radar root)")
        # The consumer of a rendered target writes reports about a checkout and
        # reads a version out of its manifest, so a remote URL is not enough.
        if str(t.get("repo", "")).startswith(("http://", "https://", "git@")) and not fb.get(
            "worktree"
        ):
            errs.append(
                "[feedback] needs `worktree` (absolute local checkout) when `repo` is a remote URL"
            )
        for k in ("worktree", "format_doc", "triage_template", "index_builder"):
            v = fb.get(k)
            if v is not None and not isinstance(v, str):
                errs.append(f"[feedback].{k} must be a string")
        ex = fb.get("extras")
        if ex is not None and (not isinstance(ex, list) or not all(isinstance(s, str) for s in ex)):
            errs.append("[feedback].extras must be a list of strings")

    tel = t.get("telemetry")
    if tel is not None:
        # `match` sees MCP tool names; `match_skill` sees a Skill call's input.skill,
        # which is the only way an own-ring tool (reached through its skills) is
        # visible in a transcript. Either alone is a usable matcher.
        # Three matchers because there are three ways a tool appears in a transcript, and
        # a tool invisible to all three cannot have measurable exit criteria:
        #   match         MCP tool names        (artifact = mcp-server)
        #   match_skill   a Skill call's input.skill   (own-ring tools, reached by skill)
        #   match_command the executable heading a Bash/PowerShell command  (artifact = cli)
        # The third arrived last and mattered most: after the 2026-07-26 de-duplication the
        # running stack is mostly CLIs, so without it telemetry saw almost none of it.
        if not tel.get("match") and not tel.get("match_skill") and not tel.get("match_command"):
            errs.append(
                "[telemetry] needs one of `match` (tool-name globs), `match_skill` "
                "(skill-id globs) or `match_command` (CLI executable globs)"
            )
        for key in ("match_skill", "match_command"):
            v = tel.get(key)
            if v is not None and (
                not isinstance(v, list) or not all(isinstance(s, str) for s in v)
            ):
                errs.append(f"[telemetry].{key} must be a list of globs")
        if not tel.get("since"):
            errs.append("[telemetry] needs `since` (YYYY-MM-DD)")
        # Optional: the tool names the entry is expected to expose. Field telemetry can
        # only see what was used, so dead weight (exposed but never called) is
        # uncomputable unless the exposed surface is declared.
        exposed = tel.get("exposed")
        if exposed is not None and not isinstance(exposed, list):
            errs.append("[telemetry].exposed must be a list of full tool names")
        # Optional: the surfaces this tool ALSO runs on, which a transcript cannot see.
        # A git hook fires inside `git commit` and a CI job runs on a server; neither
        # writes a tool call anywhere. Without the declaration the census reads those
        # tools as barely-used and flags a thin opportunity surface that is false by
        # construction - pre-commit ran in 8 of 194 sessions while firing on every
        # commit. Declaring the surface turns the FLAG into a NOTE that names it.
        also = tel.get("also_runs_in")
        if also is not None and (
            not isinstance(also, list) or not all(isinstance(s, str) and s for s in also)
        ):
            errs.append(
                "[telemetry].also_runs_in must be a list of non-empty strings naming the "
                'surfaces a transcript cannot see (e.g. "pre-commit hook", "GitHub Actions")'
            )
        # OPPORTUNITY, which is the denominator use never had. Zero measured use has three
        # readings - the work never called for it, it was not mounted, or it was there and
        # the agent never reached for it - and a count of invocations cannot tell them
        # apart. These globs name the calls that mark a session where the tool WOULD have
        # applied: a WebFetch is an opportunity for a docs server, a Grep is an opportunity
        # for a semantic-search server. Same shape as the three use matchers, and matched
        # the same way, so `taken_ratio` compares like with like.
        #
        # Optional by design, and never guessed: an opportunity glob asserts "this call
        # means the tool was applicable", which is a judgement about the tool. Written
        # where the entry's own notes already say it, nowhere else - the same rule the
        # matchers themselves are under, for the same reason (a matcher that cannot mean
        # anything manufactures a number that reads as evidence).
        for key in OPPORTUNITY_KEYS:
            v = tel.get(key)
            if v is not None and (
                not isinstance(v, list) or not all(isinstance(s, str) and s for s in v)
            ):
                errs.append(f"[telemetry].{key} must be a list of non-empty globs")

    ver = t.get("version")
    if ver is not None:
        pol = ver.get("policy")
        if pol not in VERSION_POLICIES:
            errs.append(
                f"[version].policy must be one of {'/'.join(VERSION_POLICIES)}, got {pol!r}"
            )
        if pol == "pin" and not ver.get("pinned"):
            errs.append("[version].policy=pin needs `pinned` (the exact version required)")
        if pol == "floor" and not ver.get("min"):
            errs.append("[version].policy=floor needs `min` (the lowest acceptable version)")
        # A policy other than `any` is only meaningful against a resolved registry, and a
        # registry resolved by NAME is the identity trap the backing gate forbids: querying
        # PyPI for a private tool's name returns somebody else's project, and comparing the
        # two reads as a version verdict while being two different pieces of software.
        if pol == "latest" and not t.get("registry"):
            errs.append(
                "[version].policy=latest needs `registry` - upstream cannot be resolved "
                "by name alone: a registry is taken from the tool's repository metadata, "
                "never found by matching a name"
            )
        for k in ("pinned", "min", "reason"):
            v = ver.get(k)
            if v is not None and not isinstance(v, str):
                errs.append(f"[version].{k} must be a string")

    px = t.get("pilot_exit")
    if px is not None:
        # Also allowed on ring=own. `own` states AUTHORSHIP, not exemption from
        # evidence — and an author is most biased about their own tool, so an own-ring
        # entry whose value is unproven should carry the same pre-registered criteria a
        # pilot does. Exempting them was the hole this closes.
        #
        # And allowed on any entry that WAS a pilot. Parking a pilot must not delete its
        # pre-registered criteria: they were written before the data existed, and that
        # is the only property that makes them worth anything. Forcing them out on
        # demotion would let a parked experiment come back later with fresh, conveniently
        # looser criteria — pre-registration laundering. The block stays as the record of
        # what was promised; the ring says whether it is currently being tested.
        was_pilot = any(h.get("ring") == "pilot" for h in (t.get("history") or []))
        if t.get("ring") not in ("pilot", "own") and not was_pilot:
            errs.append("[pilot_exit] is only for ring=pilot/own tools, or one previously piloted")
        for k in ("review_after_days", "adopt_if", "decline_if"):
            if not px.get(k):
                errs.append(
                    f"[pilot_exit] needs `{k}` — pre-registered BEFORE the field data exists"
                )
        if px.get("review_after_days") is not None and not isinstance(
            px.get("review_after_days"), int
        ):
            errs.append("[pilot_exit].review_after_days must be an integer (days)")
        # Optional numeric thresholds. `adopt_if`/`decline_if` are prose and are decided
        # by a human; these give field_report.py the mechanically checkable part, so a
        # number is compared against a pre-registered number rather than an intention.
        for k in ("min_sessions_with_use", "min_invocations"):
            v = px.get(k)
            if v is not None and not isinstance(v, int):
                errs.append(f"[pilot_exit].{k} must be an integer")
        rate = px.get("max_error_rate")
        if rate is not None and (not isinstance(rate, (int, float)) or not 0 <= float(rate) <= 1):
            errs.append("[pilot_exit].max_error_rate must be a fraction in 0..1")

    return errs


def missing_pilot_exit(t: dict) -> bool:
    """A pilot without exit criteria is how a pilot becomes a parking lot.

    Kept out of validate() (which is schema-shaped) and enforced by gate.py, where it
    is now a FAIL. It began as a tolerated backlog because the rule arrived after 31
    pilot entries existed and a gate that is red by construction stops being read; the
    2026-07-26 de-duplication emptied that backlog by demoting every pilot that had
    never been installed, so the leniency no longer has a reason. Two pilots, both with
    criteria — from here the cost of compliance is one block per new experiment.
    """
    return t.get("ring") == "pilot" and t.get("pilot_exit") is None


# Which artifact kinds a transcript can see at all. The three matcher kinds read MCP
# tool names, a Skill call's `skill`, and the executable heading a shell command - so a
# tool is measurable exactly when it reaches the session as one of those.
#
# The rest are not oversights: a `python-lib` is imported and leaves no tool call, a
# `cc-lsp` is driven natively by the harness, a `standard` is a format. Demanding a
# matcher there produces one that cannot match, and a matcher that cannot match is
# worse than none - it manufactures a zero that reads as evidence. That happened twice
# here (litellm measured with an MCP glob, pyright-lsp piloted with no instrument at
# all), which is why the exclusion is a rule rather than a habit.
MEASURABLE_ARTIFACTS = {"cli", "mcp-server", "cc-plugin", "corpus"}
MATCHER_KEYS = ("match", "match_skill", "match_command")

# The same three shapes, pointed at a different question: not "was the tool used" but
# "was there an occasion where it would have applied". Deliberately parallel to
# MATCHER_KEYS - one is the numerator, the other supplies a denominator that is not
# "every session on the machine".
OPPORTUNITY_KEYS = ("opportunity_match", "opportunity_match_skill", "opportunity_match_command")

# Every key a [telemetry] block may carry. Closed on purpose: a mistyped matcher name
# is not a syntax error in TOML, it is a silent zero. `match_commands` or `matches`
# would leave the entry with no matcher at all, the report would print 0 invocations,
# and the zero would read as evidence - which is the exact failure the litellm and
# context7 matchers already produced by other routes. gate.py reports strays as WARN.
TELEMETRY_KEYS = (
    *MATCHER_KEYS,
    *OPPORTUNITY_KEYS,
    "since",
    "exposed",
    "also_runs_in",
)


def unknown_telemetry_keys(t: dict) -> list[str]:
    """Keys in [telemetry] that the miner will never read. Empty means clean."""
    tel = t.get("telemetry")
    if not isinstance(tel, dict):
        return []
    return sorted(k for k in tel if k not in TELEMETRY_KEYS)


def missing_telemetry(t: dict) -> bool:
    """An own/adopt entry that CAN be measured and declares no matcher.

    The rings are claims about observed state, and until 2026-08-24 nothing
    made the claim checkable over time: 12 of the 19 own/adopt entries had no
    [telemetry] at all, including all five of the owner's own plugins - the strongest
    claims on the board were the least instrumented. Nothing reported it, because
    nothing looked.

    Same shape as missing_pilot_exit, and same reasoning: the cost of compliance is one
    block per entry, and it is paid when the entry is written rather than a year later
    when the transcripts that would have answered the question are gone. They are
    pruned at roughly two months, so an undeclared matcher is not a deferred
    measurement - it is a lost one.
    """
    if t.get("ring") not in ("own", "adopt"):
        return False
    if t.get("artifact") not in MEASURABLE_ARTIFACTS:
        return False
    # An explicit, machine-readable opt-out. The first version of this took a COMMENT as
    # the way to document a deliberate absence, and a comment is invisible to the gate -
    # so the absence was either unenforced or a permanent red, with no third option. The
    # field carries the reason and gate.py prints it on every run, which is the point:
    # an undeclared gap goes quiet, a declared one keeps arguing for itself.
    if str(t.get("telemetry_absent") or "").strip():
        return False
    tel = t.get("telemetry")
    return not tel or not any(tel.get(k) for k in MATCHER_KEYS)


# ---------------------------------------------------------------- version sites

# `## [X.Y.Z] - YYYY-MM-DD`, the single grammar every changelog in the estate uses, so one
# parser can read every repo's changelog. `[Unreleased]` deliberately does not match: it
# names no version, so it cannot be a version site.
RELEASE_HEADING = re.compile(r"^## \[(\d+\.\d+\.\d+)\] - (\d{4}-\d{2}-\d{2})\s*$", re.MULTILINE)


def release_headings(root: Path | None = None) -> list[tuple[str, str]]:
    """(version, date) for every released heading in CHANGELOG.md, newest first."""
    path = (root or data_root()) / "CHANGELOG.md"
    if not path.is_file():
        return []
    return RELEASE_HEADING.findall(path.read_text(encoding="utf-8"))


# `__version__ = "0.1.0"` at the top level of a module, read as TEXT rather than by
# importing it. Importing would answer for whichever copy of the package is on the path -
# which is precisely the wrong question when the tree being judged is a checkout that is
# not the installed one, and is no question at all in the fallback that runs the package
# straight off a PYTHONPATH. The site is a file, so it is read as a file.
_DUNDER_VERSION = re.compile(r"^__version__\s*=\s*[\"']([^\"']+)[\"']", re.MULTILINE)


def _package_version_site(base: Path) -> tuple[Path, str | None] | None:
    """The `src/<package>/__init__.py` of the project at `base`, and the version in it.

    `None` when the repository ships no package - which is the ordinary case for a data
    root, not a fault. The package is located from `[project].name`, the key that already
    says what gets built, with the PEP 503 spelling of `-` as `_`.
    """
    pyproject = base / "pyproject.toml"
    try:
        name = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["name"]
    except (OSError, KeyError, tomllib.TOMLDecodeError):
        return None
    init = base / "src" / str(name).replace("-", "_") / "__init__.py"
    if not init.is_file():
        return None
    found = _DUNDER_VERSION.search(init.read_text(encoding="utf-8"))
    return init, (found.group(1) if found else None)


def version_site_errors(root: Path | None = None) -> list[str]:
    """Disagreements between the version sites. Empty list == they agree.

    THREE SITES where there were two, and the third arrived with distribution. The pair
    was `[project].version` and CHANGELOG.md's newest RELEASED heading, and the shortness
    of that list was a property of the thing rather than an omission: nothing was
    published, so no namespace carried a version of its own. A distributed package does -
    `stack_radar.__version__` - and it is the site an installed copy answers from, so
    leaving it uncompared would be a version site with no mechanism: agreement kept by
    convention rather than by something that fails, which is the one arrangement version
    sites are not allowed.

    THE THIRD IS OPTIONAL, and that is what lets one implementation serve two kinds of
    repository. The same verb is run against a data root, which has a `pyproject.toml` and
    a `CHANGELOG.md` and no package at all; there, a missing third site is a two-site
    repository and not a finding. What is NOT optional is a package that exists and
    declares no `__version__`: that is a site that has quietly stopped existing, and it is
    reported.

    `uv.lock` is a fourth place the string appears and is deliberately not compared here:
    `uv run` re-locks before any of this could read it, so the check could never go red.
    `uv lock --check` is that file's mechanism, and CI runs it first.

    One implementation, read from three places - tests/test_version_sites.py (the failing
    test that holds the sites to agreement), gate.py (so the ritual answers the question
    too) and the `check-version-sites` verb. Two implementations of one fact drift
    silently; this repo already has a whole flag for that (`field_report.py --reconcile`).
    """
    base = root or data_root()
    pyproject = base / "pyproject.toml"
    changelog = base / "CHANGELOG.md"
    errs: list[str] = []
    if not changelog.is_file():
        return [f"no CHANGELOG.md at {changelog} - there is no version site to compare against"]
    heads = release_headings(base)
    if not heads:
        return [
            "CHANGELOG.md carries no `## [X.Y.Z] - YYYY-MM-DD` heading - the version site "
            "this check compares against does not exist"
        ]
    declared = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]
    newest, date = heads[0]
    if declared != newest:
        errs.append(
            f"pyproject.toml says {declared}, CHANGELOG.md's newest released heading says "
            f"{newest} (dated {date}) - a release rolls both in one metadata-only commit"
        )
    site = _package_version_site(base)
    if site is not None:
        init, in_package = site
        rel = init.relative_to(base).as_posix()
        if in_package is None:
            errs.append(
                f"{rel} declares no `__version__` - a package that ships carries its "
                "version in its own namespace, and this is the site an installed copy "
                "answers from. A site that stops existing stops disagreeing, silently"
            )
        elif in_package != declared:
            errs.append(
                f"pyproject.toml says {declared} and {rel} says {in_package} - they are "
                "the same fact, and a release rolls every site in one metadata-only commit"
            )
    versions = [tuple(int(p) for p in v.split(".")) for v, _ in heads]
    if versions != sorted(versions, reverse=True):
        errs.append(
            f"the release headings are not in descending version order: {versions} - a "
            "heading that does not move the version forward lets one version name two trees"
        )
    return errs


def validate_environment(e: dict) -> list[str]:
    errs = []
    if not e.get("name"):
        errs.append("missing field: name")
    rings = e.get("rings")
    if not rings:
        errs.append("missing field: rings (which rings this environment converges)")
    else:
        for r in rings:
            if r not in RINGS:
                errs.append(f"bad ring in rings: {r}")
    for key in ("require_env", "exclude"):
        v = e.get(key)
        if v is not None and not isinstance(v, list):
            errs.append(f"{key} must be a list")
    # Secrets never live here — only the NAMES of required variables.
    for name in e.get("require_env") or []:
        if "=" in str(name):
            errs.append(f"require_env holds variable NAMES only, never values: {name!r}")
    return errs


# ------------------------------------------------------------------- convergence


def tools_for_environment(env: dict, tools: list[dict] | None = None) -> list[dict]:
    """The tools an environment wants converged, after ring + exclude + per-tool opt-in."""
    tools = tools if tools is not None else load_tools()
    rings = set(env.get("rings") or [])
    excluded = set(env.get("exclude") or [])
    name = env.get("name")
    out = []
    for t in tools:
        # Existence scope first: a tool that does not exist here is not a candidate at
        # all, whatever its ring says. Checked before rings so an in-house corp entry
        # never has to pretend to a ring to stay out of the personal machine.
        exists_in = t.get("environments")
        if exists_in and name not in exists_in:
            continue
        if t.get("ring") not in rings or t.get("name") in excluded:
            continue
        inst = t.get("install") or {}
        wanted = inst.get("environments")
        if wanted and name not in wanted:
            continue
        out.append(t)
    return out
