"""Reconcile the data-plane feedback inbox with the control-plane archive.

WHY THIS EXISTS (two rules that collide, and how both hold)
-----------------------------------------------------------
The feedback corpus lives in this repository, so it travels between machines and can
be triaged across tools. The invisible-hand invariant forbids any data-plane file from
pointing here, because that would make a working session depend on the control plane:
delete the radar and the registered feedback directory would vanish under the skill's
feet.

Both hold at once with an inbox:

    session -> writes -> INBOX   {feedback_root}/<tool>/      (data plane, self-sufficient)
                            |
                       ingest v  (this script)
                         ARCHIVE feedback/<tool>/             (control plane, synced, indexed)
                            |
                       mirror v  (this script)
                         INBOX   full corpus + INDEX.md

The inbox is what the rendered `feedback-targets.toml` registers, so nothing in the
data plane names this repo. The archive is what git carries between environments.
Reports are append-only with datestamped names, so the merge is a union and needs no
conflict resolution; INDEX.md is regenerated, never merged.

If this repo disappears: the inbox keeps its last mirrored state and sessions keep
appending to it. Degraded (no cross-environment merge) but fully functional.

    radar sync-feedback --env personal [--check] [--no-mirror]
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path, PurePath

from .bootstrap import home_dir
from .paths import feedback_dir
from .radar_lib import (
    begin_command,
    default_environment,
    load_environment,
    load_tools,
    tools_for_environment,
)
from .redact import extra_homes, redact_homes, redact_name, redact_text, scoped_terms


# The index builder lives in whichever own-ring worktree declares it, never a name
# hardcoded here: a tool entry carries `[feedback].index_builder`, relative to its own
# `worktree`, and this script stays honest when the checkout moves or the entry that
# ships the builder gets renamed. Entries are tried in the order `load_tools()` already
# loads them (by file name), and the first one whose declared path RESOLVES wins - not
# the first that merely declares one. A dead key (the worktree moved, the file was
# renamed) has to fall through to the next candidate rather than stop the search, or a
# single stale entry would blind every other own-ring tool's index regeneration.
def index_builder(tools: list[dict], paths: dict) -> Path | None:
    for t in tools:
        fb = t.get("feedback") or {}
        rel = fb.get("index_builder")
        if not rel:
            continue
        wt = fb.get("worktree") or t.get("repo") or ""
        for key, val in paths.items():
            wt = str(wt).replace("{" + key + "}", str(val))
        cand = Path(str(wt).replace("\\", "/")) / str(rel)
        if cand.is_file():
            return cand
    return None


def reports(d: Path) -> list[Path]:
    if not d.is_dir():
        return []
    return sorted(p for p in d.glob("*.md") if p.name != "INDEX.md")


def _redacted(
    p: Path, terms: list[tuple[str, str]], homes: Sequence[PurePath | None] = ()
) -> bytes:
    """File content with the home directories and the scoped names mapped out, in that
    order - the order `field_report` writes in, so a scoped term cannot split a home path.
    Bytes in, bytes out: reading and writing text on Windows would translate newlines and
    make every comparison of an already-ingested report differ from itself."""
    raw = p.read_bytes().decode("utf-8", "surrogateescape")
    return redact_text(redact_homes(raw, homes), terms).encode("utf-8", "surrogateescape")


def ingest(
    inbox: Path,
    archive: Path,
    terms: list[tuple[str, str]],
    check: bool,
    homes: Sequence[PurePath | None] = (),
) -> tuple[list[str], list[str]]:
    """inbox -> archive, mapping in-house names out of both the content AND the filename,
    and the home directories out of the content.

    The filename matters as much as the content: a report called
    `2026-07-09-<in-house-name>-v1.md` leaks from the directory listing alone, and 18
    of them did. The home is the other thing a session writes without anyone choosing
    to: a report quotes the path it worked in, and the archive is tracked. The inbox
    keeps the real paths, because it lives on the machine that owns them.

    `homes` is a LIST rather than one home because the inbox merges corpora: a report
    that reaches it was not necessarily written here, and the homes this machine cannot
    find are declared (`redact.extra_homes`) instead of discovered.

    The comparison is against the REDACTED source, not the raw one. Comparing raw
    bytes would report every already-ingested report as a conflict forever, since the
    archived copy is by construction not byte-equal to what the inbox holds.
    """
    new: list[str] = []
    conflict: list[str] = []
    for p in reports(inbox):
        name = redact_name(p.name, terms)
        target = archive / name
        body = _redacted(p, terms, homes)
        if target.is_file():
            if target.read_bytes() != body:
                conflict.append(name)
            continue
        new.append(name)
        if not check:
            archive.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
    return new, conflict


def mirror(archive: Path, inbox: Path, terms: list[tuple[str, str]], check: bool) -> list[str]:
    """archive -> inbox, for reports this machine has never seen.

    Redaction makes this direction asymmetric: an archived report the inbox already
    holds under its UNREDACTED name is not new, and copying it would leave the corpus
    duplicated under two spellings. So coverage is decided on the redacted name.
    """
    covered = {redact_name(p.name, terms) for p in reports(inbox)}
    new: list[str] = []
    for p in reports(archive):
        if p.name in covered or (inbox / p.name).is_file():
            continue
        new.append(p.name)
        if not check:
            inbox.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, inbox / p.name)
    return new


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--data-root",
        help="the radar data root, instead of searching upwards for radar.toml. For CI "
        "and nothing else; it still has to name a directory that carries the "
        "marker",
    )
    ap.add_argument("--env", help="environment name (default: radar.toml's [default_environment])")
    ap.add_argument("--check", action="store_true", help="report only; write nothing")
    ap.add_argument(
        "--no-mirror",
        action="store_true",
        help="ingest only; do not push the merged corpus back to the inbox",
    )
    args = ap.parse_args()
    root = begin_command(args.data_root)
    args.env = args.env or default_environment(root)

    env = load_environment(args.env)
    if env is None:
        print(f"[FAIL] no environment named {args.env!r}")
        return 1
    paths = env.get("paths") or {}
    feedback_root = paths.get("feedback_root")
    if not feedback_root:
        print(f"[FAIL] {args.env}: [paths].feedback_root is not set")
        return 1
    inbox_root = Path(str(feedback_root).replace("\\", "/"))

    tools = load_tools()
    # Targets are scoped to the environment, not global. This took `--env` and ignored it
    # for target selection, which was a latent leak rather than a cosmetic bug: the
    # radar's `feedback/` archive is TRACKED, so the first report written for an
    # environment-scoped tool would have created `feedback/<in-house-name>/` and carried
    # the name into git — exactly what tools.local/ and check_scope_leak exist to prevent.
    # It did no damage only because the scoped entries had no reports yet.
    #
    # The index builder still sees every tool: it resolves paths for whatever it is given,
    # and narrowing it would break nothing but gains nothing either.
    targets = [t for t in tools_for_environment(env, tools) if (t.get("feedback") or {}).get("dir")]
    if not targets:
        print(f"[WARN] no tool in {args.env!r} declares [feedback]; nothing to sync")
        return 0

    # The in-house name never reaches the archive; see redact.py for why the
    # alternative (not archiving those reports at all) was worse.
    terms = scoped_terms(tools)
    # This machine's home, plus the ones declared for the machines whose reports reach
    # this inbox and whose accounts `home_dir()` cannot find (redact.extra_homes).
    homes = [home_dir(), *extra_homes(root)]
    builder = index_builder(tools, paths)
    drift = 0
    conflicts = 0

    for t in sorted(targets, key=lambda x: x["name"]):
        name = t["name"]
        inbox = inbox_root / name
        archive = feedback_dir(root) / name

        ingested, conflict = ingest(inbox, archive, terms, args.check, homes)
        conflicts += len(conflict)
        for c in conflict:
            # Same filename, different bytes: two machines used one slug on one day.
            # Never resolved automatically — the per-wave slug rule exists to prevent it.
            print(f"[FAIL] {name}: {c} differs between inbox and archive; rename one wave")

        mirrored: list[str] = []
        if not args.no_mirror:
            mirrored = mirror(archive, inbox, terms, args.check)

        if ingested or mirrored:
            drift += len(ingested) + len(mirrored)
            verb = "would ingest" if args.check else "ingested"
            verb2 = "would mirror" if args.check else "mirrored"
            bits = []
            if ingested:
                bits.append(f"{verb} {len(ingested)}")
            if mirrored:
                bits.append(f"{verb2} {len(mirrored)}")
            print(f"[NOTE] {name}: {', '.join(bits)}")

        if ingested and not args.check and builder:
            subprocess.run(
                [sys.executable, str(builder), str(archive)],
                capture_output=True,
                text=True,
                timeout=300,
            )
            # The inbox gets the regenerated index too, so a session's recurrence check
            # sees the full cross-environment corpus and not just what this machine wrote.
            idx = archive / "INDEX.md"
            if idx.is_file() and not args.no_mirror:
                inbox.mkdir(parents=True, exist_ok=True)
                shutil.copy2(idx, inbox / "INDEX.md")

    if builder is None:
        print(
            "[WARN] no tool entry resolves a [feedback].index_builder; indexes were not regenerated"
        )

    total = sum(len(reports(feedback_dir(root) / t["name"])) for t in targets)
    print(
        f"\n{len(targets)} target(s) - archive holds {total} report(s)"
        f" - {drift} file(s) {'out of sync' if args.check else 'synced'}"
        f" - {conflicts} conflict(s)"
    )
    if conflicts:
        return 1
    return 1 if (args.check and drift) else 0


if __name__ == "__main__":
    sys.exit(main())
