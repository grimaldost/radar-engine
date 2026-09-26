"""Discover a brand-new environment and write its machine-local overlay. Installs nothing.

THE PROBLEM THIS SOLVES
-----------------------
A committed environment profile cannot know the machine it will land on. `corp.toml`
ships with placeholder paths (`<home>/Documents`) precisely because guessing
would be worse than failing loudly — `apply.py --check` complains rather than inventing
a path. But somebody still has to turn those placeholders into real values, and asking
an agent to guess a home directory is exactly the kind of judgement that should not be a
judgement at all. It is computable, so it is computed here.

WHAT IS AND IS NOT AUTOMATED
----------------------------
Discovery is deterministic: paths, versions, what is already installed. That is this
script. Policy is not: whether this machine may reach an external MCP server, whether
network installs are permitted, which in-house tools exist. Those need a human or an
agent that can read the local rules, and they live in the bootstrap prompt of
`docs/new-environment.md`, in the engine's documentation, instead.

The output is `environments/<env>.local.toml`, which is gitignored. Machine-specific
paths must not travel: they are true of one workstation and false everywhere else, and a
committed profile that carries them would break every other machine that pulled it.

Read-only unless `--write` is passed. Nothing here installs, mounts or configures
anything — `apply.py` owns that, and it asks first.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

from .paths import env_dir
from .radar_lib import begin_command, with_home

# The root is IMPORTED, not redefined. This module used to compute its own
# `Path(__file__).parent.parent`, which was one of nine places in the tree that each
# decided where the radar was; they agreed only because there was one repository. The one
# case that will legitimately not import it is `init`, which creates a data root rather
# than standing in one and is therefore handed its target.
MIN_PYTHON = (3, 11)  # tomllib

# Commands worth knowing about before proposing a plan. `claude` is what makes an agent
# session possible at all; uv is how most entries install; git is how this repo arrived.
PROBES = [
    ("git", ["git", "--version"]),
    ("python", [sys.executable, "--version"]),
    ("uv", ["uv", "--version"]),
    ("node", ["node", "--version"]),
    ("npm", ["npm", "--version"]),
    ("claude", ["claude", "--version"]),
]


def run(cmd: list[str], timeout: int = 20) -> str | None:
    exe = shutil.which(cmd[0]) if not Path(cmd[0]).is_absolute() else cmd[0]
    if not exe:
        return None
    try:
        p = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [exe, *cmd[1:]],
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    out = (p.stdout or p.stderr or "").strip().splitlines()
    return out[0] if out else f"exit {p.returncode}"


def documents_dir(home: Path) -> Path | None:
    """The user's documents root, without pretending to be certain.

    Windows localises this folder and OneDrive relocates it, so a hardcoded 'Documents'
    is a guess that fails silently on a redirected profile. Candidates are probed in
    order of likelihood and the first that EXISTS wins; None is a legitimate answer and
    is reported as such rather than defaulted.
    """
    candidates = [home / "Documents", home / "documents"]
    one = os.environ.get("OneDrive") or os.environ.get("OneDriveCommercial")
    if one:
        candidates.insert(0, Path(one) / "Documents")
    for c in candidates:
        if c.is_dir():
            return c
    return None


def claude_home(home: Path) -> Path | None:
    p = home / ".claude"
    return p if p.is_dir() else None


def claude_config(home: Path) -> Path | None:
    """Claude Code's own top-level config, a SIBLING of claude_home rather than a file in it.

    Its own probe because the profiles need it as its own `[paths]` key: `reconcile.py`
    reads the mounted MCP servers from it, and deriving it from `claude_home` would be a
    second place that decides where it is.
    """
    p = home / ".claude.json"
    return p if p.is_file() else None


def home_dir() -> Path:
    """The user's home directory.

    THE ONE `Path.home()` IN THE ENGINE, and the reason it is a function here rather than a
    call at each site: this module's job is to find the machine, so the publication check
    exempts this file by name and fails the call anywhere else. A caller that needs the home
    directory - `init`, which creates a data root and therefore has no profile to read it
    from - asks this instead of reaching for the machine itself.
    """
    return Path.home()


def discovered_paths(home: Path) -> dict[str, str]:
    """The `[paths]` a profile can be given without asking anyone: every key, found or not.

    Every key is present and an absent one is the EMPTY STRING rather than missing, because
    the two callers need to tell "not found" from "not looked for": `main()` compares
    discovered against declared and must not report drift on a path it never probed, and
    `init` writes a commented-out line so the operator can see which value it failed to
    find rather than inheriting a guess.
    """
    docs, chome, cconfig = documents_dir(home), claude_home(home), claude_config(home)
    return {
        "documents": as_posix(docs) or "",
        "claude_home": as_posix(chome) or "",
        "claude_config": as_posix(cconfig) or "",
        "feedback_targets": (f"{as_posix(chome)}/feedback-targets.toml" if chome else ""),
        "feedback_root": f"{as_posix(chome)}/feedback" if chome else "",
    }


def installed_plugins(home: Path) -> list[str]:
    f = home / ".claude" / "plugins" / "installed_plugins.json"
    if not f.is_file():
        return []
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return sorted((data.get("plugins") or {}).keys())


def mounted_mcp(home: Path) -> list[str]:
    f = home / ".claude.json"
    if not f.is_file():
        return []
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    names = set((data.get("mcpServers") or {}).keys())
    for proj in (data.get("projects") or {}).values():
        if isinstance(proj, dict):
            names |= set((proj.get("mcpServers") or {}).keys())
    return sorted(names)


def load_profile_raw(root: Path, name: str) -> dict | None:
    f = env_dir(root) / f"{name}.toml"
    if not f.is_file():
        return None
    with f.open("rb") as fh:
        # Expanded like every other reader's, or a declared `~/.claude` would read as
        # drift from the discovered path it names.
        return with_home(tomllib.load(fh), home_dir())


def as_posix(p: Path | None) -> str | None:
    return p.as_posix() if p else None


def collapse_home(paths: dict[str, str], home: Path) -> dict[str, str]:
    """`[paths]` with a leading home directory written back as `~`.

    The inverse of `radar_lib.expand_home`, and the pair is the point: the starter profile
    lands in the TRACKED `environments/<env>.toml`, so it must not spell the account out.
    Every reader expands the `~` back, so the file says the same thing and names nobody.

    Only the home itself or the home followed by a separator counts. A sibling directory
    the home happens to be a prefix of - `<home>-old`, `<home>2` - is not this account's,
    and collapsing it would claim a path that is somebody else's.
    """
    h = home.as_posix()
    out: dict[str, str] = {}
    for key, value in paths.items():
        s = str(value).replace("\\", "/")
        if s == h:
            out[key] = "~"
        elif s.startswith(h + "/"):
            out[key] = "~" + s[len(h) :]
        else:
            out[key] = value
    return out


def starter_profile(name: str, paths: dict[str, str], home: Path | None = None) -> str:
    """A new profile, deliberately narrow. Widening is a decision; starting wide is a bet."""
    paths = collapse_home(paths, home or home_dir())
    body = [
        f'name = "{name}"',
        'description = "FILL IN: what this machine is and what policy applies to it."',
        "",
        "# Start with what is already measured and adopted. A pilot is an experiment, and",
        "# an experiment does not belong on a machine where a broken tool costs someone",
        '# else\'s time. Add "pilot" only once this machine is genuinely yours to break.',
        'rings = ["own", "adopt"]',
        "",
        "# Exclusions with a reason each, so a later reader knows policy from preference.",
        "exclude = []",
        "",
        "# NAMES of required environment variables, never values. apply.py checks presence",
        "# and never prints contents.",
        "require_env = []",
        "",
        "[paths]",
    ]
    for k, v in paths.items():
        body.append(f'{k} = "{v}"' if v else f'# {k} = "FILL IN: not found automatically"')
    body += [
        "",
        "[flags]",
        "# Both default to false: err toward installing nothing until the local rules are",
        "# known. Turning these on is a policy decision, not a convenience.",
        "allow_external_mcp = false",
        "allow_network_install = false",
        "",
        '# "git-remote" if this machine can reach the private repo; "git-bundle" for an',
        "# air-gapped hand-carry (append-only reports make bundles conflict-free).",
        'sync = "git-bundle"',
        "",
        f'overlay = "{name}.local.toml"',
    ]
    return "\n".join(body) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument(
        "--data-root",
        help="the radar data root, instead of searching upwards for radar.toml. For CI "
        "and nothing else; it still has to name a directory that carries the "
        "marker",
    )
    ap.add_argument("--env", required=True, help="environment name, e.g. corp")
    ap.add_argument(
        "--write",
        action="store_true",
        help="write the overlay (and a starter profile if none exists); otherwise report only",
    )
    args = ap.parse_args()
    root = begin_command(args.data_root)

    if sys.version_info < MIN_PYTHON:
        print(
            f"[FAIL] python {'.'.join(map(str, MIN_PYTHON))}+ required "
            f"(tomllib); this is {platform.python_version()}"
        )
        raise SystemExit(2)

    home = home_dir()
    docs, chome = documents_dir(home), claude_home(home)

    print(f"stack-radar bootstrap - environment {args.env!r}")
    print(f"  platform : {platform.system()} {platform.release()} ({platform.machine()})")
    print(f"  python   : {platform.python_version()}  ({sys.executable})")
    print(f"  home     : {home.as_posix()}")
    print(f"  documents: {as_posix(docs) or 'NOT FOUND - fill in by hand'}")
    print(f"  claude   : {as_posix(chome) or 'NOT FOUND - is Claude Code installed?'}")
    print()

    print("  commands found:")
    missing = []
    for label, cmd in PROBES:
        ver = run(cmd)
        print(f"    {label:8} {ver or '-- not found'}")
        if ver is None:
            missing.append(label)
    print()

    plugins, servers = installed_plugins(home), mounted_mcp(home)
    print(f"  plugins already installed : {', '.join(plugins) or 'none'}")
    print(f"  mcp servers already mounted: {', '.join(servers) or 'none'}")
    print()

    discovered = discovered_paths(home)

    profile = load_profile_raw(root, args.env)
    overlay_path = env_dir(root) / f"{args.env}.local.toml"

    if profile is None:
        print(f"  no committed profile environments/{args.env}.toml")
        text = starter_profile(args.env, discovered)
        if args.write:
            dest = env_dir(root) / f"{args.env}.toml"
            dest.write_text(text, encoding="utf-8")
            print(f"  [WROTE] {dest.relative_to(root)} - review it, then commit it")
        else:
            print("  proposed starter profile (re-run with --write to create it):\n")
            print("\n".join("    " + ln for ln in text.splitlines()))
        raise SystemExit(0)

    declared = profile.get("paths") or {}
    drift = {
        k: v
        for k, v in discovered.items()
        if v and str(declared.get(k, "")).replace("\\", "/") != v
    }

    print("  declared vs discovered paths:")
    for k in discovered:
        d, f = str(declared.get(k, "-")), discovered[k] or "-"
        mark = "ok " if k not in drift else "->"
        print(f"    {mark} {k:17} declared={d}")
        if k in drift:
            print(f"       {'':17} actual  ={f}")
    print()

    if not drift:
        print("  every declared path matches this machine; no overlay needed.")
    else:
        lines = [
            f'name = "{args.env}"',
            "",
            "# Machine-local overlay written by `radar bootstrap`. GITIGNORED on purpose:",
            "# these paths are true of this workstation and false of every other one, so a",
            "# committed profile carrying them would break the next machine that pulled it.",
            "# Layered over the committed profile by apply.py's resolver.",
            "",
            "[paths]",
        ]
        lines += [f'{k} = "{v}"' for k, v in drift.items()]
        text = "\n".join(lines) + "\n"
        if args.write:
            overlay_path.write_text(text, encoding="utf-8")
            print(f"  [WROTE] {overlay_path.relative_to(root)} ({len(drift)} path(s))")
        else:
            print(f"  proposed {overlay_path.name} (re-run with --write):\n")
            print("\n".join("    " + ln for ln in text.splitlines()))
    print()

    print("  next:")
    if missing:
        print(f"    - install first: {', '.join(missing)}")
    print(f"    - radar apply --env {args.env} --plan      # writes nothing")
    print("    - radar gate                             # 0 FAIL expected")
    print(
        "    - then read docs/new-environment.md in the engine's documentation for the "
        "policy decisions"
    )


if __name__ == "__main__":
    main()
