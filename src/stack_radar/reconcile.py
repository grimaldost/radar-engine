"""Reconcile declared rings against observed reality. Proposes, never edits.

WHY THIS EXISTS
---------------
The radar's mission says adoption is MEASURED, never taken on faith — and then the
first seeding assigned `adopt` in bulk from *other people's* download counts. The
result: 22 entries claimed ring=adopt while exactly one was installed. A ring that
records an intention rather than a state re-inflates a month after every cleanup,
because nothing compares the label to the world.

This script is that comparison. For each entry it gathers evidence the machine can
actually produce:

  installed     the [install].check probe passes (reuses apply.py's prober, so an
                unrunnable probe is UNKNOWN and never silently "absent")
  projects      the package appears in a real dependency table of some project under
                the projects root — parsed with tomllib, never substring-matched
  precommit     the name appears as a pre-commit hook id or hook repo
  mcp_mounted   the server appears in Claude Code's mcpServers config

and then proposes a ring the evidence supports. Rules, deliberately blunt:

  adopt  needs presence: installed, or a real dependency, or a mounted server.
  pilot  needs the same. A pilot is an EXPERIMENT; one that was never installed is
         not an experiment, it is a bookmark — and 31 bookmarks in the pilot ring is
         how the ring stops meaning anything.
  observe is the honest home for "interesting, not running". It costs nothing and
         claims nothing, which is exactly why it must be the default.

Nothing here edits a file. Demotion is a judgement with a history entry attached, and
the operator writes it — same discipline as apply.py, which proposes removals and
never performs them.

Evidence absence is not the same as evidence of absence: entries whose probe is
UNKNOWN, whose install kind is `none` (a standard has nothing to install), or that
are excluded by the profile are reported as NOTE and never proposed for demotion.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path

from .apply import PRESENT, UNKNOWN, expand, load_profile, probe
from .radar_lib import begin_command, default_environment, load_tools, tools_for_environment

# Where the user's actual projects live: the directory the data root sits in. The control
# plane READS the data plane — that is the permitted direction; the reverse
# would be the breach. Derived in main() rather than here, because a module-level default
# would resolve the root at import time and a command would need a marker merely to print
# its own `--help`.

# Rings whose claim requires evidence. `own` is authored (its presence is the
# working tree itself, which apply.py already probes); observe/discard claim nothing.
EVIDENCE_RINGS = ("adopt", "pilot")

DEP_TABLE_KEYS = (
    ("project", "dependencies"),
    ("project", "optional-dependencies"),
    ("dependency-groups",),
    ("tool", "uv", "dev-dependencies"),
    ("tool", "poetry", "dependencies"),
    ("tool", "poetry", "group"),
)

# A requirement string ("pandera[polars]>=0.20,<1 ; python_version>'3.10'") reduced to
# its distribution name. Extras/versions are noise for identity; the MARKER is not, so it
# matters, so has_marker() keeps it.
REQ_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def dist_name(req: str) -> str:
    m = REQ_NAME.match(req or "")
    return (m.group(1) if m else "").lower().replace("_", "-")


def norm(name: str) -> str:
    return str(name or "").strip().lower().replace("_", "-")


def has_marker(req: str) -> bool:
    """Whether the requirement is conditional on an environment marker.

    Evaluating markers properly needs `packaging`, which is not stdlib — so instead of
    guessing, a conditional dependency is counted SEPARATELY and never allowed to be the
    sole evidence of adoption. That is what caught `inhouse-lib; python_version < '0'`:
    a marker that can never be true, i.e. a deliberately disabled dependency, which the
    first version of this script counted as a real consumer.
    """
    return ";" in (req or "")


def walk_dep_strings(node, out: list[str]) -> None:
    """Collect requirement strings from arbitrarily nested dependency tables.

    Dependency layout varies (PEP 621 lists, dependency-groups maps, poetry's
    name->constraint maps, group.<name>.dependencies). Rather than special-case each
    shape, walk it: strings are requirements, dict KEYS are also names under poetry's
    mapping style, and anything else recurses.
    """
    if isinstance(node, str):
        out.append(node)
    elif isinstance(node, list):
        for v in node:
            walk_dep_strings(v, out)
    elif isinstance(node, dict):
        for k, v in node.items():
            out.append(str(k))
            walk_dep_strings(v, out)


def dig(d: dict, path: tuple[str, ...]):
    cur = d
    for k in path:
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur


def scan_projects(
    root: Path,
) -> tuple[dict[str, set[str]], dict[str, set[str]], dict[str, set[str]]]:
    """(deps -> consumers, conditional deps -> projects, config tokens -> projects).

    Parsed, not grepped. A substring search would report `ty` as used by every project
    that depends on `typing-extensions` or configures `mypy` — the same name-matching
    error the backing gate already forbids for registries.

    THREE WAYS THIS OVERCOUNTED, all found by the owner rejecting a number it produced
    ("inhouse-lib is not a dependency of anything here"). It had claimed 9 consumers:

    1. **Self-reference.** Declaring your own extras is the standard PEP 621 idiom
       (`inhouse-lib[datatools]` inside inhouse-lib' own pyproject). Six of the nine
       were the tool depending on itself. A project is not its own adopter.
    2. **Sibling worktrees.** tu-v13-base and tu-v13-w1..w4 are five checkouts of ONE
       codebase — same `project.name`. Counting directories counted the same consumer
       five times, so projects are now keyed by declared `project.name`.
    3. **Disabled dependencies.** `inhouse-lib; python_version < '0'` carries a marker
       that can never be true. Conditional requirements are tracked separately and can
       never be the sole evidence of adoption.

    The direction of the earlier conclusions survives (ruff/pytest/mypy/ty were still the
    incumbents and basedpyright/pyrefly still had nothing), but the magnitudes were
    inflated, and a script whose whole purpose is to replace faith with counting has no
    business reporting a count it cannot defend.
    """
    deps: dict[str, set[str]] = {}
    conditional: dict[str, set[str]] = {}
    tokens: dict[str, set[str]] = {}
    # directory -> declared project name, so the pre-commit pass below counts the same
    # identities as the dependency pass. Without it five worktrees of one codebase would
    # be deduplicated in one half of the evidence and counted five times in the other.
    dir_to_project: dict[str, str] = {}
    for pj in sorted(root.glob("*/pyproject.toml")):
        try:
            with pj.open("rb") as fh:
                data = tomllib.load(fh)
        except (OSError, tomllib.TOMLDecodeError):
            continue
        # Identity is the declared distribution name, not the folder. Falling back to the
        # folder keeps non-PEP-621 trees countable.
        own = norm((data.get("project") or {}).get("name") or "") or norm(pj.parent.name)
        dir_to_project[pj.parent.name] = own
        raw: list[str] = []
        for path in DEP_TABLE_KEYS:
            walk_dep_strings(dig(data, path), raw)
        for r in raw:
            n = dist_name(r)
            if not n or n == own:
                continue
            (conditional if has_marker(r) else deps).setdefault(n, set()).add(own)
        # A `[tool.<x>]` section is configuration, which is adoption evidence of a
        # different kind: the tool may be invoked by pre-commit or CI rather than
        # being a project dependency.
        for name in data.get("tool") or {}:
            tokens.setdefault(str(name).lower(), set()).add(own)

    for pc in sorted(root.glob("*/.pre-commit-config.yaml")):
        proj = dir_to_project.get(pc.parent.name, norm(pc.parent.name))
        try:
            text = pc.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # No yaml in the stdlib. Hook ids and repo slugs are line-shaped enough that
        # two narrow regexes beat a dependency, and a miss here only ever WEAKENS a
        # demotion proposal (it never invents evidence).
        for m in re.finditer(r"^\s*-?\s*id:\s*([A-Za-z0-9._-]+)", text, re.M):
            tokens.setdefault(m.group(1).lower(), set()).add(proj)
        for m in re.finditer(r"^\s*-?\s*repo:\s*\S*?/([A-Za-z0-9._-]+?)(?:\.git)?\s*$", text, re.M):
            tokens.setdefault(m.group(1).lower(), set()).add(proj)
    return deps, conditional, tokens


def mounted_mcp_servers(paths: dict[str, str]) -> tuple[set[str], str]:
    """Server names Claude Code has mounted, from its own config.

    Plugin-provided and claude.ai-connector servers do not appear here; they are not
    `claude mcp add` entries. That is why an empty set is reported as such rather
    than as proof of nothing running.

    `[paths].claude_config` rather than `Path.home() / ".claude.json"`: the file
    sits directly in the home directory, a sibling of `claude_home` and not under it, so
    deriving it from the machine the SCRIPT happens to run on - rather than from the
    declared profile - would silently read the wrong machine's config on any environment
    other than the operator's own.
    """
    declared = paths.get("claude_config")
    if not declared:
        return set(), "no [paths].claude_config declared"
    cfg = Path(declared)
    if not cfg.exists():
        return set(), f"no {cfg}"
    try:
        data = json.loads(cfg.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return set(), f"unreadable: {exc}"
    names = set((data.get("mcpServers") or {}).keys())
    for proj in (data.get("projects") or {}).values():
        if isinstance(proj, dict):
            names |= set((proj.get("mcpServers") or {}).keys())
    return {n.lower() for n in names}, "ok"


def identities(t: dict) -> set[str]:
    """The names this entry could legitimately appear under in the data plane."""
    out = {str(t["name"]).lower().replace("_", "-")}
    reg = str(t.get("registry") or "")
    if ":" in reg:
        out.add(reg.split(":", 1)[1].lower().replace("_", "-"))
    for alias in t.get("aliases") or []:
        out.add(str(alias).lower().replace("_", "-"))
    return {o for o in out if o}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument(
        "--data-root",
        help="the radar data root, instead of searching upwards for radar.toml. For CI "
        "and nothing else; it still has to name a directory that carries the "
        "marker",
    )
    ap.add_argument("--env", help="environment name (default: radar.toml's [default_environment])")
    ap.add_argument(
        "--projects-root",
        help="where the user's projects live (default: the directory holding the data root)",
    )
    ap.add_argument("--timeout", type=int, default=20)
    ap.add_argument(
        "--no-probe",
        action="store_true",
        help="skip install probes (offline / fast); presence then rests on deps + mcp only",
    )
    ap.add_argument(
        "--strict",
        action="store_true",
        help="exit 1 when any entry claims a ring the evidence does not support",
    )
    args = ap.parse_args()
    root = begin_command(args.data_root)
    args.env = args.env or default_environment(root)

    env, errs = load_profile(args.env)
    if env is None:
        print(f"no such environment: {args.env}", file=sys.stderr)
        raise SystemExit(2)
    for e in errs:
        print(f"[profile] {e}")

    proot = Path(args.projects_root) if args.projects_root else root.parent
    deps, conditional, tokens = scan_projects(proot)
    paths = {k: str(v) for k, v in (env.get("paths") or {}).items()}
    servers, mcp_state = mounted_mcp_servers(paths)

    print(f"stack-radar reconcile - environment {args.env!r}")
    print(f"  projects root: {proot}  ({len(deps)} distinct deps seen)")
    print(f"  mounted mcp servers: {sorted(servers) or 'none'} ({mcp_state})")
    print()

    wanted = {t["name"] for t in tools_for_environment(env)}
    excluded = set(env.get("exclude") or [])
    rows, proposals, notes = [], [], []

    for t in sorted(load_tools(), key=lambda x: x["name"]):
        ring, name = t.get("ring"), t["name"]
        if ring not in EVIDENCE_RINGS:
            continue
        ids = identities(t)
        inst = t.get("install") or {}
        kind = inst.get("kind")

        dep_hits = sorted({p for i in ids for p in deps.get(i, set())})
        cond_hits = sorted({p for i in ids for p in conditional.get(i, set())})
        tok_hits = sorted({p for i in ids for p in tokens.get(i, set())})
        mcp_hit = bool(ids & servers)

        state, detail = UNKNOWN, ""
        if args.no_probe or not inst.get("check"):
            state, detail = (
                UNKNOWN,
                "no check probe" if not inst.get("check") else "skipped",
            )
        else:
            state, detail = probe(expand(inst["check"], paths), args.timeout)

        evidence = []
        if state == PRESENT:
            evidence.append("installed")
        if dep_hits:
            evidence.append(f"dep in {len(dep_hits)} project(s)")
        if tok_hits:
            evidence.append(f"configured in {len(tok_hits)}")
        # Reported, never counted as evidence: a conditional requirement may be disabled
        # (`; python_version < '0'`) and markers cannot be evaluated without `packaging`.
        if cond_hits and not dep_hits:
            notes.append(
                f"{name}: {len(cond_hits)} conditional-only reference(s) "
                f"({', '.join(cond_hits)}) - behind an environment marker, not counted"
            )
        if mcp_hit:
            evidence.append("mcp mounted")

        rows.append((name, ring, state, ", ".join(evidence) or "-"))

        if name in excluded:
            notes.append(f"{name}: excluded by profile ({ring}); not judged here")
            continue
        if name not in wanted:
            notes.append(f"{name}: not wanted in {args.env}; not judged here")
            continue
        if kind == "none":
            notes.append(f"{name}: install.kind=none (a standard/spec) - unprobeable by design")
            continue
        if evidence:
            continue
        # Absence of evidence is not evidence of absence. An unrun or unrunnable probe
        # leaves presence genuinely unknown, so the entry is reported and left alone —
        # including under --no-probe, where EVERY probe is unrun and demoting on that
        # would manufacture dozens of false positives out of a flag meant to go faster.
        if state == UNKNOWN and inst.get("check"):
            why = "probe skipped (--no-probe)" if args.no_probe else f"probe UNKNOWN ({detail})"
            notes.append(f"{name}: {why} - presence unknown, not demoted")
            continue

        why = "no install probe" if not inst.get("check") else "absent"
        proposals.append(
            (
                name,
                ring,
                f"{why}; 0 project deps; 0 configs" + ("" if not mcp_state else ""),
            )
        )

    w = max((len(r[0]) for r in rows), default=4)
    print(f"{'tool'.ljust(w)}  ring    probe     evidence")
    print(f"{'-' * w}  ------  --------  --------")
    for name, ring, state, ev in rows:
        print(f"{name.ljust(w)}  {ring:6}  {state:8}  {ev}")

    if notes:
        print()
        for n in notes:
            print(f"[NOTE] {n}")

    print()
    if proposals:
        print(
            f"{len(proposals)} entr{'y' if len(proposals) == 1 else 'ies'} claim a ring the evidence does not support:"
        )
        for name, ring, why in proposals:
            print(f"  demote  {name}: {ring} -> observe   ({why})")
        print()
        print("Each demotion is a judgement: edit tools/<name>.toml (ring + a dated")
        print("[[history]] entry naming this evidence), then re-run `radar gate`. This")
        print("command never edits - a ring change is a decision, and decisions carry authorship.")
    else:
        print("every adopt/pilot entry is backed by observed evidence.")

    raise SystemExit(1 if (args.strict and proposals) else 0)


if __name__ == "__main__":
    main()
