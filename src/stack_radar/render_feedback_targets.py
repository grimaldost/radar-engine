"""Render an environment's feedback-targets file from the own-ring [feedback] blocks.

The output is a data-plane artefact: a flat TOML file of registered dogfooding
targets that the consuming skill reads directly. As a data-plane file it must be
self-sufficient and must not point back here — so the generated header says the
file is generated and hand-edits are lost, but never says by what, and the file
carries no path into this repo's scripts. Delete this repo and the artefact still
resolves exactly as it reads.

Content is a pure function of the inputs (no timestamp), so re-running is a no-op
and `--check` is meaningful. Paths in a `[feedback]` block may use the same
`{documents}` / `{claude_home}` placeholders as an `[install]` block, so one entry
renders correctly on every machine.

    radar render-feedback-targets --env personal [--stdout|--check]
"""

from __future__ import annotations

import sys
from pathlib import Path

from .radar_lib import begin_command, load_environment, tools_for_environment

SCHEMA = "feedback-targets/v1"

HEADER = """# feedback-targets - the registered dogfooding feedback destinations.
#
# GENERATED FILE - do not hand-edit. It is rewritten wholesale on every
# regeneration and hand edits are lost; change the source of the binding instead.
#
# Plain data, self-sufficient: absolute paths, no includes, no tooling required to
# read it, and no dependency on whatever wrote it. There is deliberately no
# timestamp - the content is a pure function of its inputs, so an unchanged
# binding regenerates byte-identically.
"""


def toml_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def posix(p: str | Path) -> str:
    # Paths are stored with Windows separators in places; the artefact is
    # single-quoted-free TOML, so normalise to forward slashes and escape nothing.
    return Path(str(p).replace("\\", "/")).as_posix()


def subst(s: str, paths: dict) -> str:
    """Expand the environment profile's [paths] placeholders, as install blocks do."""
    for key in ("documents", "claude_home", "feedback_root"):
        v = paths.get(key)
        if v:
            s = s.replace("{" + key + "}", posix(v))
    return s


def worktree_of(tool: dict, paths: dict) -> str | None:
    """The local checkout the consumer reads manifests and sources from."""
    fb = tool.get("feedback") or {}
    wt = fb.get("worktree")
    if wt:
        return posix(subst(str(wt), paths))
    repo = str(tool.get("repo", ""))
    if repo.startswith(("http://", "https://", "git@")):
        return None
    return posix(subst(repo, paths))


def render(env: dict) -> tuple[str, list[str]]:
    """The artefact text, plus findings. FAIL findings mean do not write."""
    findings: list[str] = []
    lines = [HEADER, f"schema = {toml_str(SCHEMA)}", ""]
    paths = env.get("paths") or {}
    inbox_root = posix(paths["feedback_root"]) if paths.get("feedback_root") else None

    targets = [t for t in tools_for_environment(env) if t.get("feedback")]
    for tool in sorted(targets, key=lambda t: t["name"]):
        name = tool["name"]
        fb = tool["feedback"]
        rel = fb.get("dir")
        if not rel:
            findings.append(f"[FAIL] {name}: [feedback] has no `dir`")
            continue
        wt = worktree_of(tool, paths)
        if not wt:
            findings.append(
                f"[FAIL] {name}: remote `repo` and no [feedback].worktree - "
                "the consumer needs a local checkout"
            )
            continue
        if not Path(wt).is_dir():
            findings.append(f"[WARN] {name}: worktree not present here: {wt}")

        # The registered destination is the data-plane INBOX, never this repo's archive.
        # Pointing the consumer at the archive would put a control-plane path into a
        # data-plane file and make the stack depend on the radar's presence: delete the
        # radar and the skill would have nowhere to write. The radar ingests from the
        # inbox and mirrors the merged corpus back (`radar sync-feedback`).
        if not inbox_root:
            findings.append(f"[FAIL] {name}: [paths].feedback_root is not set for this environment")
            continue
        lines.append(f"[targets.{name}]")
        lines.append(f"repo = {toml_str(wt)}")
        lines.append(f"feedback_dir = {toml_str(posix(Path(inbox_root) / name))}")
        for key in ("format_doc", "triage_template"):
            v = fb.get(key)
            if not v:
                continue
            p = posix(Path(wt) / v)
            if not Path(p).is_file():
                findings.append(f"[WARN] {name}: {key} not found: {p}")
            lines.append(f"{key} = {toml_str(p)}")
        extras = fb.get("extras") or []
        if extras:
            lines.append("extras = [")
            for e in extras:
                lines.append(f"  {toml_str(e)},")
            lines.append("]")
        lines.append("")

    if not targets:
        findings.append(f"[WARN] {env['name']}: no tool in this environment declares [feedback]")
    return "\n".join(lines).rstrip("\n") + "\n", findings


def main() -> None:
    argv = sys.argv[1:]
    # No argparse here, so `--data-root` is read from argv by the resolver.
    begin_command()
    if "--env" not in argv:
        print("usage: radar render-feedback-targets --env <name> [--stdout|--check]")
        sys.exit(2)
    env_name = argv[argv.index("--env") + 1]
    env = load_environment(env_name)
    if env is None:
        print(f"[FAIL] no environment named {env_name!r} in environments/")
        sys.exit(1)

    text, findings = render(env)
    fails = sum(1 for f in findings if f.startswith("[FAIL]"))
    for f in findings:
        print(f)

    if "--stdout" in argv:
        if not fails:
            print(text, end="" if text.endswith("\n") else "\n")
        sys.exit(1 if fails else 0)

    out = (env.get("paths") or {}).get("feedback_targets")
    if not out:
        print(f"[FAIL] {env_name}: [paths].feedback_targets is not set")
        sys.exit(1)
    dest = Path(posix(out))
    if not dest.parent.is_dir():
        print(
            f"[FAIL] {env_name}: {dest.parent} does not exist - run this on that "
            "machine, or use --stdout"
        )
        sys.exit(1)
    if fails:
        print(f"\nnot written ({fails} FAIL)")
        sys.exit(1)

    current = dest.read_text(encoding="utf-8") if dest.is_file() else None
    if "--check" in argv:
        if current == text:
            print(f"[NOTE] {dest} is current")
            sys.exit(0)
        print(f"[FAIL] {dest} is stale or missing - regenerate")
        sys.exit(1)

    # The artefact promises these inbox directories; make them real so the consumer's
    # first write does not have to. They live in the data plane and outlive this repo.
    root = (env.get("paths") or {}).get("feedback_root")
    if root:
        for tool in tools_for_environment(env):
            if (tool.get("feedback") or {}).get("dir"):
                (Path(posix(root)) / tool["name"]).mkdir(parents=True, exist_ok=True)

    if current == text:
        print(f"[NOTE] {dest} unchanged")
    else:
        dest.write_text(text, encoding="utf-8")
        print(f"wrote {dest}")


if __name__ == "__main__":
    main()
