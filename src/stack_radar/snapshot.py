"""Refresh metrics for all tools into snapshots/<today>.json.

Sources: gh api (stars/watchers/pushed/archived/license) for GitHub repos, and
the registry named in each tool's `registry` field for downloads (npm:X or
pypi:X — per the identity rule, the registry field is set from repo metadata,
never name-matching). Network-slow by design (pypistats rate limits); run
monthly or before a radar review, then run gate.py against the fresh snapshot.
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import subprocess
import time
import urllib.request

from .paths import snap_dir
from .radar_lib import begin_command, latest_snapshot, load_tools

GH_RE = re.compile(r"github\.com/([^/]+/[^/\s]+)")


def gh_repo(slug: str) -> dict:
    try:
        out = subprocess.run(
            [
                "gh",
                "api",
                f"repos/{slug}",
                "--jq",
                "{stars: .stargazers_count, watchers: .subscribers_count, "
                'pushed: .pushed_at[0:10], archived: .archived, license: (.license.spdx_id // "none")}',
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        ).stdout
        return json.loads(out)
    except Exception as e:  # noqa: BLE001 - a snapshot keeps going on any per-repo failure
        return {"error": str(e)[:120]}


def fetch_json(url: str) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            return json.load(r)
    except Exception:  # noqa: BLE001
        return None


def downloads(registry: str) -> tuple[int | None, str | None]:
    kind, _, pkg = registry.partition(":")
    if kind == "npm":
        d = fetch_json(f"https://api.npmjs.org/downloads/point/last-month/{pkg}")
        return (d or {}).get("downloads"), registry
    if kind == "pypi":
        d = fetch_json(f"https://pypistats.org/api/packages/{pkg}/recent")
        time.sleep(8)  # pypistats rate limit
        return ((d or {}).get("data") or {}).get("last_month"), registry
    return None, None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument(
        "--data-root",
        help="the radar data root, instead of searching upwards for radar.toml. For CI "
        "and nothing else; it still has to name a directory that carries the "
        "marker",
    )
    args = ap.parse_args()
    root = begin_command(args.data_root)
    # A REFRESH MUST NOT LOSE EVIDENCE.
    # Downloads come from a rate-limited third party, so a lookup fails sometimes. The
    # first version simply omitted the field on failure, which silently ERASED a known
    # number — and the astroturf detector, which needs downloads to fire, went quiet.
    # Observed on 2026-07-26: three catalogue entries lost their download figures in one
    # refresh while their star counts kept climbing, so the gate went from 4 astroturf
    # flags to 1 and looked healthier while knowing strictly less. A monitor that improves
    # its own report by forgetting is worse than no monitor.
    # So a failed lookup carries the previous value forward, marked with the date it was
    # actually measured. Stale-but-labelled beats absent-and-silent.
    _, prev = latest_snapshot()
    snap = {}
    carried = 0
    dropped = 0
    today = datetime.date.today().isoformat()
    for t in load_tools():
        name = t["name"]
        entry: dict = {}
        m = GH_RE.search(t.get("repo", ""))
        if m:
            entry |= gh_repo(m.group(1))
        if t.get("registry"):
            dl, src = downloads(t["registry"])
            if dl is not None:
                entry["downloads_month"] = dl
                entry["downloads_source"] = src
        # Carry forward whenever THIS pass produced no figure, not only when a lookup
        # failed. The 2026-07-26 fix guarded the failed-lookup path and left this one
        # open, so the same three entries lost their numbers again on 2026-08-24 and
        # the gate went from 5 astroturf flags to 2 while knowing strictly less.
        #
        # The gap is structural rather than accidental: those three entries declare NO
        # `registry` at all - their figures came from the verified catalogue,
        # hand-measured, precisely because the astroturf case is
        # "huge star count, and the package is not really distributed anywhere". So the
        # entries that most need a download number are the ones least able to refresh
        # it, and gating carry-forward on `registry` dropped exactly them.
        #
        # Their stars meanwhile went 93k->101k, 63k->67k and 27k->31k. A monitor that
        # improves its own report by forgetting is worse than no monitor.
        if "downloads_month" not in entry:
            old = prev.get(name) or {}
            old_source = old.get("downloads_source")
            # A figure is only evidence about THIS entry while the registry it was
            # measured under still applies. `downloads_source` records that registry
            # (set beside `downloads_month` above), so a mismatch against the entry's
            # current `registry` - removed, or pointed somewhere else - means the old
            # number belongs to an identity this entry no longer claims, and carrying
            # it forward would be carrying forward someone else's evidence. Observed on
            # a catalogue entry whose wrong `registry` was corrected away after a figure
            # had already been measured under it: the figure the wrong registry had
            # produced kept being carried forward and flagged as this entry's own.
            #
            # An entry that has NEVER declared a registry records no `downloads_source`
            # at all (`old_source` is None) - there is nothing to compare, so that is
            # not a mismatch, and a hand-measured figure keeps carrying forward exactly
            # as before.
            registry_changed = old_source is not None and old_source != t.get("registry")
            if old.get("downloads_month") is not None and not registry_changed:
                entry["downloads_month"] = old["downloads_month"]
                entry["downloads_source"] = old_source
                entry["downloads_as_of"] = old.get("downloads_as_of") or "earlier"
                entry["downloads_stale"] = True
                carried += 1
            elif registry_changed:
                dropped += 1
                now = t.get("registry")
                where = f"declares {now!r}" if now else "declares no `registry` at all"
                print(
                    f"[NOTE] {name}: dropped a stale download figure measured under "
                    f"{old_source!r} - the entry now {where}; a figure from a "
                    "different registry is not evidence about this one"
                )
        if entry:
            if "downloads_month" in entry and not entry.get("downloads_stale"):
                entry["downloads_as_of"] = today
            snap[name] = entry
            print(f"{name}: {entry}")
    out = snap_dir(root) / f"{today}.json"
    out.write_text(json.dumps(snap, indent=1, sort_keys=True), encoding="utf-8")
    print(f"\nwrote {out} ({len(snap)} entries)")
    if carried:
        print(
            f"[NOTE] {carried} entr{'y' if carried == 1 else 'ies'} kept an earlier "
            "download figure because the registry lookup failed (marked downloads_stale); "
            "a refresh never erases a measurement it could not repeat"
        )
    if dropped:
        print(
            f"[NOTE] {dropped} entr{'y' if dropped == 1 else 'ies'} dropped a download "
            "figure measured under a registry the entry no longer declares - a stale "
            "number under the WRONG identity is worse than none"
        )


if __name__ == "__main__":
    main()
