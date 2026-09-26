"""Converge an environment onto its profile: plan by default, execute only on request.

This is the radar's hand on the stack. It observes the machine with each tool's own
`[install].check` probe, diffs that against what `environments/<name>.toml` wants, and
prints a plan. Installs it proposes and (with --apply --yes) executes; removals it only
ever proposes, because the radar does not know what else on this machine depends on a
package, and a wrong uninstall costs more than a missing tool.

The asymmetry is the point of the invisible-hand invariant: what apply writes into the
data plane is a plain `uv tool` / `npm -g` / `claude mcp` artefact that keeps working
with the radar deleted, and nothing it writes names the radar. apply itself creates no
files.

    radar apply --env personal --check      # plan only, writes nothing
    radar apply --env personal --apply      # show what it would run, stop
    radar apply --env personal --apply --yes

Exit 0 when the environment is converged, 1 when anything needs action.
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass

from .paths import env_dir
from .radar_lib import (
    RINGS,
    begin_command,
    load_environments,
    load_tools,
    tools_for_environment,
    validate_environment,
    with_home,
)

CHECK_TIMEOUT = 60
APPLY_TIMEOUT = 900

PRESENT = "yes"
ABSENT = "no"
UNKNOWN = "unknown"
UNPROBED = "-"

MCP_KINDS = ("mcp-server", "mcp-plugin")
# Kinds apply.py may execute. Everything else is verify-only or printed as a suggestion.
EXECUTABLE_KINDS = (
    "uv-tool",
    "npm-tool",
    "pip",
    "claude-plugin",
    "mcp-server",
    "mcp-plugin",
)

PLACEHOLDER = re.compile(r"\{([A-Za-z0-9_]+)\}")


# ----------------------------------------------------------------------- reporting


@dataclass
class Row:
    name: str
    ring: str
    wanted: str
    present: str
    action: str


class Report:
    """Findings in house format. FAIL is the only class that fails the run on its own."""

    def __init__(self) -> None:
        self.fails = 0
        self.warns = 0
        self.drift = 0

    def fail(self, subject: str, msg: str) -> None:
        print(f"[FAIL] {subject}: {msg}")
        self.fails += 1

    def warn(self, subject: str, msg: str) -> None:
        print(f"[WARN] {subject}: {msg}")
        self.warns += 1

    def note(self, subject: str, msg: str) -> None:
        print(f"[NOTE] {subject}: {msg}")


# -------------------------------------------------------------------------- profile


def base_profile(name: str) -> dict | None:
    """The profile named `name`, preferring environments/<name>.toml.

    Overlay files live in the same directory, so matching on the `name` key alone can
    pick the overlay instead of the profile it overlays.
    """
    envs = load_environments()
    for e in envs:
        if e.get("name") == name and e.get("_file") == f"{name}.toml":
            return e
    for e in envs:
        if e.get("name") == name:
            return e
    return None


def merge_overlay(base: dict, over: dict) -> dict:
    """Layer an overlay on a profile: lists that accumulate accumulate, the rest replaces.

    `exclude` and `require_env` concatenate (an overlay adds local policy, it does not
    relax the base). Tables ([paths], [flags]) merge key by key so an overlay that sets
    one flag does not silently drop the paths. Everything else replaces.
    """
    merged = dict(base)
    for key, value in over.items():
        if key in ("_file", "name"):
            continue
        if key in ("exclude", "require_env"):
            seen = list(merged.get(key) or [])
            for item in value:
                if item not in seen:
                    seen.append(item)
            merged[key] = seen
        elif isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = {**merged[key], **value}
        else:
            merged[key] = value
    return merged


def load_profile(name: str) -> tuple[dict | None, list[str]]:
    """(merged profile, source descriptions). The overlay is optional and gitignored."""
    base = base_profile(name)
    if base is None:
        return None, []
    sources = [f"environments/{base.get('_file', name + '.toml')}"]
    # corp.toml declares `overlay` inside [flags]; accept it at the top level too.
    overlay_name = base.get("overlay") or (base.get("flags") or {}).get("overlay")
    if overlay_name:
        path = env_dir() / str(overlay_name)
        if path.exists():
            with path.open("rb") as fh:
                over = tomllib.load(fh)
            # The base is expanded already (`load_environments`); the overlay's own
            # `[paths]` are expanded here, or a `~` in it would reach a consumer.
            base = merge_overlay(base, with_home(over))
            sources.append(f"environments/{overlay_name} (overlay)")
        else:
            sources.append(f"environments/{overlay_name} (overlay absent)")
    return base, sources


def expand(template: str, paths: dict[str, str]) -> str:
    """Fill {key} from the profile's [paths]. Unknown keys are an error, not a guess."""

    def sub(m: re.Match[str]) -> str:
        key = m.group(1)
        if key not in paths:
            raise KeyError(key)
        return str(paths[key])

    return PLACEHOLDER.sub(sub, template)


# ----------------------------------------------------------------------- observation


def run_command(cmd: str, timeout: int) -> tuple[str, int | None, str]:
    """Run one command, no shell. Returns (outcome, returncode, detail).

    outcome: "ran" (returncode is meaningful), "missing" (not on PATH), "error".
    No shell means no pipes and no expansion, which keeps a probe the same command on
    every machine. argv[0] is resolved with which() because CreateProcess on Windows
    does not honour PATHEXT, so a .cmd shim (npm, npx, most global npm installs) is
    invisible to a bare subprocess call.
    """
    try:
        argv = shlex.split(cmd)
    except ValueError as exc:
        return "error", None, f"unparseable command: {exc}"
    if not argv:
        return "error", None, "empty command"
    exe = shutil.which(argv[0])
    if exe is None:
        return "missing", None, f"{argv[0]} not on PATH"
    try:
        proc = subprocess.run(
            [exe, *argv[1:]],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return "error", None, f"timed out after {timeout}s"
    except OSError as exc:
        return "error", None, f"could not run: {exc}"
    first = ""
    for stream in (proc.stdout, proc.stderr):
        line = (stream or "").strip().splitlines()
        if line:
            first = line[0].strip()[:110]
            break
    return "ran", proc.returncode, first


def probe(cmd: str, timeout: int) -> tuple[str, str]:
    """Observe presence. A probe that cannot run is UNKNOWN, never silently absent."""
    outcome, rc, detail = run_command(cmd, timeout)
    if outcome == "missing":
        return ABSENT, detail
    if outcome == "error":
        return UNKNOWN, detail
    return (PRESENT if rc == 0 else ABSENT), (detail or f"exit {rc}")


# ------------------------------------------------------------------------- planning


@dataclass
class Job:
    name: str
    command: str


#  Paths the radar CREATES rather than paths the machine must already have. Counting
#  these as drift made --check unsatisfiable: it could not go green until the renderer
#  had run, and the renderer's output is not this script's business. Their PARENT is
#  what has to exist; freshness of the artefact belongs to
#  `render_feedback_targets.py --check`.
OUTPUT_PATHS = {"feedback_targets", "feedback_root"}


def check_paths(env: dict, rep: Report) -> dict[str, str]:
    paths = {k: str(v) for k, v in (env.get("paths") or {}).items()}
    for key, value in sorted(paths.items()):
        if os.path.exists(value):
            continue
        if key in OUTPUT_PATHS:
            parent = os.path.dirname(value.rstrip("/\\")) or value
            if not os.path.isdir(parent):
                rep.warn("paths", f"{key}: parent {parent} does not exist")
                rep.drift += 1
            continue
        rep.warn("paths", f"{key} declared as {value} does not exist")
        rep.drift += 1
    return paths


def check_env_vars(env: dict, rep: Report) -> None:
    names = list(env.get("require_env") or [])
    if not names:
        return
    missing = [n for n in names if not os.environ.get(str(n))]
    for n in missing:
        # Presence only. The value is never read, printed, or logged.
        rep.fail("require_env", f"{n} is not set in this environment")
    rep.note("require_env", f"{len(names) - len(missing)}/{len(names)} variables present")


def plan(
    env: dict,
    paths: dict[str, str],
    rep: Report,
    timeout: int,
    verbose: bool,
) -> tuple[list[Row], list[Job], list[Job]]:
    """Diff the machine against the profile. Returns (rows, installs, manual steps)."""
    tools = load_tools()
    wanted = tools_for_environment(env, tools)
    wanted_names = {t["name"] for t in wanted}
    flags = env.get("flags") or {}
    allow_mcp = bool(flags.get("allow_external_mcp", True))
    allow_network = bool(flags.get("allow_network_install", True))
    excluded = set(env.get("exclude") or [])

    rows: list[Row] = []
    installs: list[Job] = []
    manual: list[Job] = []
    unmanaged: list[str] = []

    def observe(name: str, inst: dict) -> tuple[str, str]:
        raw = inst.get("check")
        if not raw:
            return UNPROBED, "no check probe declared"
        try:
            cmd = expand(str(raw), paths)
        except KeyError as exc:
            rep.fail(name, f"check references unknown [paths] key: {exc.args[0]}")
            return UNKNOWN, "unresolved placeholder"
        status, detail = probe(cmd, timeout)
        if verbose:
            print(f"       probe {name}: {cmd} -> {status} ({detail})")
        return status, detail

    for t in sorted(wanted, key=lambda t: (RINGS.index(t["ring"]), t["name"])):
        name = t["name"]
        inst = t.get("install")
        if inst is None:
            unmanaged.append(name)
            rows.append(Row(name, t["ring"], "yes", UNPROBED, "unmanaged"))
            continue
        kind = str(inst.get("kind"))

        if kind in MCP_KINDS and not allow_mcp:
            rep.note(name, f"skipped: allow_external_mcp=false (kind={kind})")
            rows.append(Row(name, t["ring"], "yes", UNPROBED, "skip: policy"))
            continue
        if kind == "none":
            rows.append(Row(name, t["ring"], "yes", UNPROBED, "nothing to install"))
            continue

        status, detail = observe(name, inst)
        if status == PRESENT:
            rows.append(Row(name, t["ring"], "yes", status, "ok"))
            continue
        if status == UNKNOWN:
            rep.warn(name, f"probe could not decide presence: {detail}")
            rows.append(Row(name, t["ring"], "yes", status, "investigate"))
            rep.drift += 1
            continue

        # Absent, or present-unknowable (manual entries with no honest machine probe).
        instruction = str(inst.get("instruction") or "").strip()
        try:
            instruction = expand(instruction, paths) if instruction else ""
        except KeyError as exc:
            rep.fail(name, f"instruction references unknown [paths] key: {exc.args[0]}")
            instruction = ""

        if kind == "repo":
            rep.warn(name, f"working tree missing - {instruction or 'restore it by hand'}")
            manual.append(Job(name, instruction or "restore the working tree"))
            rows.append(Row(name, t["ring"], "yes", status, "clone (manual)"))
            rep.drift += 1
            continue

        if kind == "manual" or kind not in EXECUTABLE_KINDS:
            if status == UNPROBED:
                # Nothing to converge machine-wide (a library, a per-project choice).
                rep.note(name, f"manual, not machine-scoped: {instruction}")
                rows.append(Row(name, t["ring"], "yes", status, "manual"))
            else:
                rep.note(name, f"absent, install by hand: {instruction}")
                manual.append(Job(name, instruction))
                rows.append(Row(name, t["ring"], "yes", status, "manual"))
                rep.drift += 1
            continue

        raw_apply = str(inst.get("apply") or "")
        try:
            cmd = expand(raw_apply, paths)
        except KeyError as exc:
            rep.fail(name, f"apply references unknown [paths] key: {exc.args[0]}")
            rows.append(Row(name, t["ring"], "yes", status, "broken entry"))
            continue
        if not allow_network:
            rep.note(name, f"allow_network_install=false - run by hand: {cmd}")
            manual.append(Job(name, cmd))
            rows.append(Row(name, t["ring"], "yes", status, "manual (no network)"))
            rep.drift += 1
            continue
        installs.append(Job(name, cmd))
        rows.append(Row(name, t["ring"], "yes", status, f"install ({kind})"))
        rep.drift += 1

    # One line, not one per tool: an entry with no [install] block is a gap in the
    # catalog, not drift on this machine, and it must not drown the actionable findings.
    if unmanaged:
        rep.note(
            "unmanaged",
            f"{len(unmanaged)} wanted entries carry no [install] block: " + ", ".join(unmanaged),
        )

    # Removals are proposed for what this profile actively rejects: a discarded tool, or
    # one the profile excludes. A tool in an unconverged ring (observe) is neither wanted
    # nor unwanted, so it is left alone rather than nagged about.
    for t in sorted(tools, key=lambda t: t["name"]):
        name = t["name"]
        if name in wanted_names:
            continue
        rejected = t["ring"] == "discard" or name in excluded
        inst = t.get("install") or {}
        if not rejected or not inst.get("check"):
            continue
        status, detail = observe(name, inst)
        if status != PRESENT:
            continue
        why = "ring=discard" if t["ring"] == "discard" else "excluded by this profile"
        # `instruction` says how to INSTALL, so echoing it here would invert the advice.
        # Only an entry that spells out its own removal gets a command; otherwise say
        # plainly that the operator decides, because the radar cannot see what else on
        # this machine depends on the package.
        hint = str(inst.get("remove") or "").strip()
        rep.note(
            name,
            f"present but not wanted here ({why}) - "
            + (f"suggested removal: {hint}" if hint else "removal left to you")
            + " (apply never uninstalls)",
        )
        rows.append(Row(name, t["ring"], "no", status, "propose removal"))
        rep.drift += 1

    return rows, installs, manual


# --------------------------------------------------------------------------- output


def render_table(rows: list[Row]) -> None:
    header = ("tool", "ring", "wanted", "present", "action")
    data = [(r.name, r.ring, r.wanted, r.present, r.action) for r in rows]
    widths = [
        max(len(header[i]), *(len(d[i]) for d in data)) if data else len(header[i])
        for i in range(len(header))
    ]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print("\n" + fmt.format(*header))
    print("  ".join("-" * w for w in widths))
    for d in data:
        print(fmt.format(*d))


def execute(installs: list[Job], rep: Report) -> int:
    failed = 0
    for job in installs:
        print(f"\n$ {job.command}")
        outcome, rc, detail = run_command(job.command, APPLY_TIMEOUT)
        if outcome != "ran":
            rep.fail(job.name, f"install could not run: {detail}")
            failed += 1
        elif rc != 0:
            rep.fail(job.name, f"install exited {rc}: {detail}")
            failed += 1
        else:
            print(f"       ok: {detail}" if detail else "       ok")
    return failed


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Plan or apply convergence of an environment's tool stack."
    )
    ap.add_argument(
        "--data-root",
        help="the radar data root, instead of searching upwards for radar.toml. For CI "
        "and nothing else; it still has to name a directory that carries the "
        "marker",
    )
    ap.add_argument("--env", required=True, help="environment profile name")
    ap.add_argument("--check", action="store_true", help="plan only (default)")
    ap.add_argument("--plan", action="store_true", help="alias for --check")
    ap.add_argument("--apply", action="store_true", help="execute the plan's installs")
    ap.add_argument("--yes", action="store_true", help="required for --apply to run")
    ap.add_argument("--timeout", type=int, default=CHECK_TIMEOUT, help="probe timeout, s")
    ap.add_argument("--verbose", action="store_true", help="print every probe")
    args = ap.parse_args()
    begin_command(args.data_root)

    applying = args.apply and not (args.check or args.plan)
    mode = "apply" if applying else "check"

    env, sources = load_profile(args.env)
    if env is None:
        names = sorted(str(e.get("name")) for e in load_environments() if e.get("name"))
        print(f"[FAIL] env: no profile named {args.env!r} (have: {', '.join(names)})")
        sys.exit(1)

    rep = Report()
    print(f"stack-radar apply - environment {args.env!r}, mode {mode}")
    for s in sources:
        print(f"  profile: {s}")
    flags = env.get("flags") or {}
    print(
        "  rings: {} | allow_network_install={} | allow_external_mcp={}".format(
            ",".join(env.get("rings") or []),
            bool(flags.get("allow_network_install", True)),
            bool(flags.get("allow_external_mcp", True)),
        )
    )
    if env.get("exclude"):
        print(f"  exclude: {', '.join(str(x) for x in env['exclude'])}")
    print()

    for err in validate_environment(env):
        rep.fail("profile", err)

    paths = check_paths(env, rep)
    check_env_vars(env, rep)
    rows, installs, manual = plan(env, paths, rep, args.timeout, args.verbose)
    render_table(rows)

    present = sum(1 for r in rows if r.present == PRESENT and r.wanted == "yes")
    wanted = sum(1 for r in rows if r.wanted == "yes")
    print(
        f"\n{wanted} wanted - {present} present - {rep.drift} needing action"
        f" - {rep.fails} FAIL, {rep.warns} WARN"
    )

    failed = 0
    if applying:
        if not installs and not manual:
            print("\nnothing to execute")
        if manual:
            print("\nmanual steps (never executed by apply):")
            for job in manual:
                print(f"  {job.name}: {job.command}")
        if installs and not args.yes:
            print("\nwould execute (re-run with --yes):")
            for job in installs:
                print(f"  {job.name}: {job.command}")
        elif installs:
            print(f"\nexecuting {len(installs)} install(s)")
            failed = execute(installs, rep)
            print(
                f"\n{len(installs) - failed}/{len(installs)} install(s) succeeded."
                " Re-run --check to confirm convergence"
                " (a fresh shell may be needed for new PATH entries)."
            )
    elif manual or installs:
        print("\nproposed:")
        for job in installs:
            print(f"  install  {job.name}: {job.command}")
        for job in manual:
            print(f"  by hand  {job.name}: {job.command}")

    sys.exit(1 if (rep.fails or rep.drift or failed) else 0)


if __name__ == "__main__":
    main()
