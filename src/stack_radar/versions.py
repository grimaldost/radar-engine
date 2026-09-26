"""Compare installed versions against upstream, under a declared policy. Upgrades nothing.

WHY THIS EXISTS
---------------
The radar tracked WHETHER a tool was present and never WHICH VERSION, so "installed" and
"current" were the same word. Two things this session made concrete:

- `pre-commit` sat a patch behind (4.6.0 vs 4.6.1) with nothing to notice it.
- Three plugins were running four months stale in one surface and current in another,
  which changed observable behaviour: a hook and its env-var gate existed in the old
  version and had been REMOVED in the new one, so the same `settings.json` was live in
  one session type and dead config in the other.

Neither is caught by presence probes, and neither is caught by staleness of the upstream
repo (which `gate.py` already checks) — an actively-developed tool with a fresh last-push
date is exactly the kind you fall behind.

THE IDENTITY RULE, which matters more here than anywhere
--------------------------------------------------------
A version comparison needs a registry, and resolving a registry by NAME is the trap the
backing gate already forbids. It is not hypothetical: a short, ordinary tool name
queried on PyPI has returned an unrelated third-party package at a LOWER version,
which next to the local one reads as "you are ahead of upstream" — a meaningless
comparison between two different projects. So a check
runs ONLY for an entry that declares `registry`, and a private tool that declares none is
reported as unversioned rather than guessed at.

POLICIES
--------
    latest  warn when behind the registry's newest release (the default for a tool you
            install from a registry: you want to know, and you decide)
    pin     fail on any deviation from `pinned` — for a tool whose behaviour you have
            measured at one version and must not have change under you
    floor   fail below `min`, silent above it — when a fix or feature is required but
            newer is fine
    any     never compared; for a tool where the version is not a meaningful axis

Reports only. Upgrading is a decision with a blast radius, and this script does not make
decisions — same discipline as apply.py (never executes a removal) and reconcile.py
(never edits a ring).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

from .radar_lib import (
    begin_command,
    default_environment,
    load_environment,
    load_tools,
    tools_for_environment,
)

SEMVERISH = re.compile(r"(\d+\.\d+(?:\.\d+)?(?:[-.][0-9A-Za-z.]+)?)")
POLICIES = ("latest", "pin", "floor", "any")
TIMEOUT = 25


def parse_version(text: str) -> str | None:
    """First version-looking token in a `--version` output.

    Tools print wildly different shapes ('ruff 0.16.0', 'Python 3.13.12',
    'ty 0.0.63 (46f4915e6 2026-07-23)', 'git version 2.53.0.windows.1'), so the first
    semver-ish run wins rather than trying to model each format.
    """
    m = SEMVERISH.search(text or "")
    return m.group(1) if m else None


def key(v: str) -> tuple:
    """Comparable tuple. Non-numeric suffixes sort BEFORE their release (0.1.0rc1 < 0.1.0)."""
    head = re.split(r"[-+]", v or "", maxsplit=1)[0]
    parts: list[tuple[int, int | str]] = []
    for chunk in head.split("."):
        m = re.match(r"^(\d+)(.*)$", chunk)
        if m:
            parts.append((0, int(m.group(1))))
            if m.group(2):
                parts.append((-1, m.group(2)))
        else:
            parts.append((-1, chunk))
    return tuple(parts)


def global_probe_env() -> dict[str, str]:
    """The environment a GLOBAL-tool probe should search with - this process's own,
    minus the running project's virtualenv.

    This script is itself run with `uv run`, which puts the project's `.venv/Scripts`
    (Windows) or `.venv/bin` (POSIX) ahead of PATH and sets `VIRTUAL_ENV` to it, so the
    project can find its own pinned tools. A version PROBE wants the opposite: it is
    asking "what does the operator have installed globally", and a bare
    `shutil.which()` against this process's PATH answers a different question - it
    resolved the project's pinned `ruff` (a dev dependency of this very repo) ahead of
    the global one, and reported the global install "behind" upstream when it was
    current, because the project-pinned copy was never behind at all.

    `VIRTUAL_ENV` is dropped and its own bin/Scripts directory is stripped out of PATH,
    by directory identity (`os.path.normcase` + `normpath`, not a string prefix, so a
    trailing separator or a differently-cased drive letter still matches on Windows).
    Everything else on PATH - the operator's real global installs - passes through
    unchanged. A probe run with no virtualenv active is unaffected either way.
    """
    env = dict(os.environ)
    venv = env.pop("VIRTUAL_ENV", None)
    if not venv:
        return env
    venv_bin = os.path.normcase(
        os.path.normpath(str(Path(venv) / ("Scripts" if os.name == "nt" else "bin")))
    )
    parts = [
        p
        for p in env.get("PATH", "").split(os.pathsep)
        if p and os.path.normcase(os.path.normpath(p)) != venv_bin
    ]
    env["PATH"] = os.pathsep.join(parts)
    return env


def run(cmd: str) -> str | None:
    """Run a probe without a shell, against the GLOBAL environment (`global_probe_env`),
    never the running project's own virtualenv. None when it cannot run at all."""
    try:
        argv = cmd.split()
        env = global_probe_env()
        exe = shutil.which(argv[0], path=env.get("PATH"))
        if not exe:
            return None
        p = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [exe, *argv[1:]],
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            shell=False,
            env=env,
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return (p.stdout or "") + (p.stderr or "")


def latest_pypi(pkg: str) -> tuple[str | None, str]:
    url = f"https://pypi.org/pypi/{pkg}/json"
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as r:  # noqa: S310 - fixed host
            return json.load(r)["info"]["version"], "ok"
    except urllib.error.HTTPError as e:
        return None, f"http {e.code} (not on PyPI under this name?)"
    except (OSError, ValueError, KeyError) as e:
        return None, f"{type(e).__name__}"


def latest_npm(pkg: str) -> tuple[str | None, str]:
    url = f"https://registry.npmjs.org/{pkg}/latest"
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as r:  # noqa: S310 - fixed host
            return json.load(r)["version"], "ok"
    except urllib.error.HTTPError as e:
        return None, f"http {e.code} (not on npm under this name?)"
    except (OSError, ValueError, KeyError) as e:
        return None, f"{type(e).__name__}"


def upstream(registry: str) -> tuple[str | None, str]:
    if ":" not in registry:
        return None, "malformed registry (want 'pypi:name' or 'npm:name')"
    kind, name = registry.split(":", 1)
    if kind == "pypi":
        return latest_pypi(name)
    if kind == "npm":
        return latest_npm(name)
    return None, f"unsupported registry kind {kind!r}"


def plugin_skew(paths: dict[str, str]) -> list[str]:
    """Plugin versions per surface, because 'installed' is not one place.

    Claude Code's CLI resolves plugins from ~/.claude/plugins/cache; a desktop-app session
    can serve its own pinned copy from a session directory. When those diverge the SAME
    settings.json means different things in the two surfaces — a hook present in one and
    removed in the other — which is invisible to every other check here.

    `home` is derived from the profile's declared `[paths].claude_home` rather than
    `Path.home()`, so a comparison against a non-default environment reads that
    environment's machine and not whichever one happens to run the script.
    """
    out: list[str] = []
    claude_home = paths.get("claude_home")
    if not claude_home:
        return ["[NOTE] plugin skew: no [paths].claude_home declared"]
    home = Path(claude_home).parent
    cache = home / ".claude" / "plugins" / "installed_plugins.json"
    cached: dict[str, str] = {}
    if cache.is_file():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            for full, installs in (data.get("plugins") or {}).items():
                name = full.split("@", 1)[0]
                for i in installs:
                    cached[name] = str(i.get("version") or "?")
        except (OSError, ValueError):
            out.append("[WARN] installed_plugins.json unreadable")

    sessions = home / "AppData" / "Roaming" / "Claude" / "local-agent-mode-sessions"
    served: dict[str, set[str]] = {}
    if sessions.is_dir():
        for pj in sessions.glob("*/*/rpm/plugin_*/.claude-plugin/plugin.json"):
            try:
                d = json.loads(pj.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            n, v = d.get("name"), str(d.get("version") or "?")
            if n:
                served.setdefault(str(n), set()).add(v)

    for name in sorted(set(cached) & set(served)):
        others = {v for v in served[name] if v != cached[name]}
        if others:
            out.append(
                f"[WARN] plugin {name}: CLI cache has {cached[name]}, an app session "
                f"serves {', '.join(sorted(others))} - the same settings.json can be live "
                "in one surface and dead config in the other"
            )
    for name in sorted(set(served) - set(cached)):
        out.append(
            f"[NOTE] plugin {name}: served to an app session "
            f"({', '.join(sorted(served[name]))}) but absent from the CLI cache"
        )
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument(
        "--data-root",
        help="the radar data root, instead of searching upwards for radar.toml. For CI "
        "and nothing else; it still has to name a directory that carries the "
        "marker",
    )
    ap.add_argument("--env", help="environment name (default: radar.toml's [default_environment])")
    ap.add_argument("--offline", action="store_true", help="skip registry lookups")
    ap.add_argument(
        "--strict",
        action="store_true",
        help="exit 1 on any policy violation (pin/floor)",
    )
    ap.add_argument("--no-plugins", action="store_true", help="skip the plugin-skew check")
    args = ap.parse_args()
    root = begin_command(args.data_root)
    args.env = args.env or default_environment(root)

    env = load_environment(args.env)
    if env is None:
        print(f"[FAIL] no environment named {args.env!r}", file=sys.stderr)
        return 2

    tools = tools_for_environment(env, load_tools())
    rows, findings, fails = [], [], 0

    for t in sorted(tools, key=lambda x: x["name"]):
        name = t["name"]
        ver = t.get("version") or {}
        policy = ver.get("policy") or ("latest" if t.get("registry") else "any")
        if policy not in POLICIES:
            findings.append(f"[FAIL] {name}: bad [version].policy {policy!r}")
            fails += 1
            continue

        check = (t.get("install") or {}).get("check")
        have = parse_version(run(check) or "") if check else None

        if policy == "any":
            rows.append((name, have or "-", "-", "any", ""))
            continue

        if policy == "pin":
            want = ver.get("pinned")
            if not want:
                findings.append(f"[FAIL] {name}: policy=pin needs `pinned`")
                fails += 1
                continue
            verdict = "ok" if have == want else ("UNKNOWN" if not have else "PINNED->DRIFT")
            if verdict == "PINNED->DRIFT":
                findings.append(
                    f"[FAIL] {name}: pinned at {want}, installed {have} - a pin exists "
                    f"because behaviour was measured at that version"
                )
                fails += 1
            rows.append((name, have or "?", want, "pin", verdict))
            continue

        if policy == "floor":
            floor = ver.get("min")
            if not floor:
                findings.append(f"[FAIL] {name}: policy=floor needs `min`")
                fails += 1
                continue
            if have and key(have) < key(floor):
                findings.append(f"[FAIL] {name}: below required floor {floor} (have {have})")
                fails += 1
                verdict = "BELOW FLOOR"
            else:
                verdict = "ok" if have else "UNKNOWN"
            rows.append((name, have or "?", f">={floor}", "floor", verdict))
            continue

        # policy == latest
        reg = t.get("registry")
        if not reg:
            # Never resolve a registry by name. Querying PyPI for a private tool's name
            # returns somebody else's project, and comparing the two is worse than silence.
            rows.append((name, have or "-", "-", "latest", "no registry declared"))
            continue
        if args.offline:
            rows.append((name, have or "-", "-", "latest", "offline"))
            continue
        up, note = upstream(str(reg))
        if not up:
            rows.append((name, have or "-", "-", "latest", note))
            continue
        if not have:
            rows.append((name, "?", up, "latest", "installed version unknown"))
            continue
        if key(have) < key(up):
            rows.append((name, have, up, "latest", "BEHIND"))
            findings.append(f"[WARN] {name}: {have} installed, {up} available ({reg})")
        elif key(have) > key(up):
            rows.append((name, have, up, "latest", "ahead of registry"))
        else:
            rows.append((name, have, up, "latest", "ok"))

    print(f"stack-radar versions - environment {args.env!r}")
    print()
    w = max((len(r[0]) for r in rows), default=4)
    print(f"{'tool'.ljust(w)}  installed    expected     policy  state")
    print(f"{'-' * w}  -----------  -----------  ------  -----")
    for n, have, want, pol, state in rows:
        print(f"{n.ljust(w)}  {have:11}  {want:11}  {pol:6}  {state}")

    if not args.no_plugins:
        paths = {k: str(v) for k, v in (env.get("paths") or {}).items()}
        skew = plugin_skew(paths)
        if skew:
            print()
            for s in skew:
                print(s)
                if s.startswith("[WARN]"):
                    findings.append(s)

    if findings:
        print()
        for f in findings:
            if not f.startswith("[WARN] plugin"):
                print(f)

    behind = sum(1 for r in rows if r[4] == "BEHIND")
    print()
    print(
        f"{len(rows)} entr{'y' if len(rows) == 1 else 'ies'} checked - {behind} behind - "
        f"{fails} policy violation(s)"
    )
    if not any(t.get("version") for t in tools):
        print(
            "[NOTE] no entry declares a [version] block yet, so everything with a registry "
            "defaults to policy=latest. Add `pin` where a measurement depends on the "
            "version, and `floor` where a fix is required."
        )
    return 1 if (args.strict and fails) else 0


if __name__ == "__main__":
    raise SystemExit(main())
