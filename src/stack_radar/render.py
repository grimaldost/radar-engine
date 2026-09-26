"""Render README.md, the radar page, and (with --public) the filtered public projection."""

from __future__ import annotations

import argparse
import datetime
import json

from .radar_lib import (
    ARTIFACTS,
    AXES,
    NO_REPO_SENTINEL,
    RINGS,
    begin_command,
    load_marker,
    load_tools,
)

# What each artifact class actually commits you to. Rendered alongside the grouping so
# the taxonomy carries its consequence rather than being a label to sort by.
ARTIFACT_MEANS = {
    "cc-plugin": "loads into the agent: skills, agents, commands, hooks",
    "mcp-server": "tool surface in the agent's context - costs tokens every session",
    "cc-lsp": "language server, driven natively by the harness; no tool surface",
    "cli": "a command in a shell or CI; no context cost, easy to audit",
    "python-lib": "imported by project code, so it becomes a runtime dependency",
    "service": "a server and UI somebody has to operate, authenticate and patch",
    "standard": "nothing to install - a format to target",
    "corpus": "authored knowledge, not executable",
    "unknown": "never established; rejected on evidence before its nature mattered",
}

# The fallback when a catalogue declares no `[render].boundary_note` (`radar init` seeds
# new catalogues with this). No decision-record citations here: a catalogue's decisions
# are its own, not a claim the engine can make on a stranger's behalf. A catalogue that
# already has a write-up of its own boundary keeps it in its own radar.toml, verbatim.
DEFAULT_BOUNDARY_NOTE = (
    "Boundary: this radar owns adoption state (rings, transitions, evidence) and the "
    "feedback registration it renders into the data plane. The stack it governs keeps "
    "its own configuration elsewhere, and neither file points back into the other."
)

HTML_TEMPLATE = """<!doctype html><html><head><meta charset="utf-8"><title>__TITLE__</title>
<style>body{font:14px system-ui;margin:20px;display:flex;gap:24px;flex-wrap:wrap}svg{flex:none}
h3{margin:8px 0 2px}ul{margin:2px 0;padding-left:18px}li{line-height:1.5}</style></head>
<body><svg id="r" width="760" height="760"></svg><div id="legend"></div>
<script>
const DATA=__DATA__;
const RINGS=["own","adopt","pilot","observe","discard"];
const AXES=__AXES__;
const COLORS={own:"#6b5b95",adopt:"#2e7d32",pilot:"#1565c0",observe:"#ef6c00",discard:"#9e9e9e"};
const svg=document.getElementById("r"),cx=380,cy=380,R0=40,dR=64;
function el(n,a){const e=document.createElementNS("http://www.w3.org/2000/svg",n);for(const k in a)e.setAttribute(k,a[k]);svg.appendChild(e);return e}
RINGS.forEach((r,i)=>{el("circle",{cx:cx,cy:cy,r:R0+dR*(i+1),fill:"none",stroke:"#ddd"});const t=el("text",{x:cx+4,y:cy-(R0+dR*i+dR/2),fill:"#999","font-size":11});t.textContent=r});
AXES.forEach((a,i)=>{const ang=2*Math.PI*i/AXES.length-Math.PI/2;const rMax=R0+dR*RINGS.length;el("line",{x1:cx,y1:cy,x2:cx+Math.cos(ang)*rMax,y2:cy+Math.sin(ang)*rMax,stroke:"#eee"});const lx=cx+Math.cos(ang+0.31)*(rMax-8),ly=cy+Math.sin(ang+0.31)*(rMax-8);const t=el("text",{x:lx,y:ly,fill:"#bbb","font-size":10,"text-anchor":"middle"});t.textContent=a});
function hash(s){let h=0;for(const c of s)h=(h*31+c.charCodeAt(0))>>>0;return h}
DATA.forEach(d=>{const ai=AXES.indexOf(d.axis),ri=RINGS.indexOf(d.ring);const h=hash(d.name);
const ang=2*Math.PI*(ai+0.15+0.7*((h%97)/97))/AXES.length-Math.PI/2;
const rad=R0+dR*ri+dR*(0.2+0.6*((h>>8)%89)/89);
const c=el("circle",{cx:cx+Math.cos(ang)*rad,cy:cy+Math.sin(ang)*rad,r:5,fill:COLORS[d.ring]});
const t=document.createElementNS("http://www.w3.org/2000/svg","title");t.textContent=d.name+" ("+d.axis+")";c.appendChild(t)});
const lg=document.getElementById("legend");
RINGS.forEach(r=>{const n=DATA.filter(d=>d.ring===r);if(!n.length)return;
const h=document.createElement("h3");h.textContent=r+" ("+n.length+")";h.style.color=COLORS[r];lg.appendChild(h);
const ul=document.createElement("ul");n.sort((a,b)=>a.name.localeCompare(b.name)).forEach(d=>{const li=document.createElement("li");li.textContent=d.name+" - "+d.axis;ul.appendChild(li)});lg.appendChild(ul)});
</script></body></html>
"""


def repo_cell(t: dict) -> str:
    """`repo` as a table cell: a link, unless it is the admitted sentinel for a tool
    with no repository - linking to the sentinel's literal text would render a
    Markdown link to nowhere, e.g. `[phantom-tool]((repo does not exist))`."""
    if t["repo"] == NO_REPO_SENTINEL:
        return t["name"]
    return f"[{t['name']}]({t['repo']})"


def ring_tables(tools: list[dict], public: bool) -> list[str]:
    lines = []
    for ring in RINGS:
        rows = [t for t in tools if t["ring"] == ring]
        if not rows:
            continue
        rows.sort(key=lambda t: (ARTIFACTS.index(t["artifact"]), t["axis"], t["name"]))
        lines.append(f"\n## {ring.capitalize()} ({len(rows)})\n")
        if public:
            lines.append("| tool | kind | axis | license |")
            lines.append("|---|---|---|---|")
            for t in rows:
                lines.append(f"| {repo_cell(t)} | {t['artifact']} | {t['axis']} | {t['license']} |")
        else:
            lines.append("| tool | kind | axis | license | eval | note |")
            lines.append("|---|---|---|---|---|---|")
            for t in rows:
                ev = t.get("eval_status", "unmeasured")
                lines.append(
                    f"| {repo_cell(t)} | {t['artifact']} | {t['axis']} | "
                    f"{t['license']} | {ev} | {t['note']} |"
                )
    return lines


def artifact_view(tools: list[dict]) -> list[str]:
    """The stack grouped by WHAT each thing is, not by which problem it addresses.

    Two views of one set. The ring tables answer "how committed am I"; this answers
    "what am I running, and where" — the question that decides context cost and blast
    radius. An axis mixes a library with a hosted service and hides that difference.
    """
    lines = [
        "\n# By kind of artifact\n",
        "`axis` says which problem a tool addresses. `artifact` says what you install "
        "and where it runs - which is what decides context cost, blast radius, and "
        "whether a locked-down environment can have it.\n",
        "| kind | own | adopt | pilot | observe | discard | what it commits you to |",
        "|---|--:|--:|--:|--:|--:|---|",
    ]
    for a in ARTIFACTS:
        group = [t for t in tools if t["artifact"] == a]
        if not group:
            continue
        cells = [str(sum(1 for t in group if t["ring"] == r) or "-") for r in RINGS]
        lines.append(f"| **{a}** | " + " | ".join(cells) + f" | {ARTIFACT_MEANS[a]} |")

    # The running stack only. A watchlist entry commits you to nothing, so listing 64
    # observe entries here would bury the eight things that actually load.
    live = [t for t in tools if t["ring"] in ("own", "adopt", "pilot")]
    lines.append("\n## In the loop or on the machine\n")
    for a in ARTIFACTS:
        group = sorted((t for t in live if t["artifact"] == a), key=lambda t: t["name"])
        if not group:
            continue
        names = ", ".join(f"`{t['name']}` ({t['ring']})" for t in group)
        lines.append(f"- **{a}** — {ARTIFACT_MEANS[a]}: {names}")
    return lines


def transitions(tools: list[dict], limit: int = 40) -> list[str]:
    entries = []
    for t in tools:
        for h in t.get("history", []):
            entries.append(
                (
                    str(h.get("date", "")),
                    t["name"],
                    h.get("ring", "?"),
                    h.get("reason", ""),
                )
            )
    entries.sort(reverse=True)
    lines = [
        "\n## Transitions (latest)\n",
        "| date | tool | ring | reason |",
        "|---|---|---|---|",
    ]
    for d, n, r, reason in entries[:limit]:
        lines.append(f"| {d} | {n} | {r} | {reason} |")
    return lines


def radar_page(tools: list[dict], path, title: str) -> None:
    data = [{"name": t["name"], "axis": t["axis"], "ring": t["ring"]} for t in tools]
    html = (
        HTML_TEMPLATE.replace("__TITLE__", title)
        .replace("__DATA__", json.dumps(data))
        .replace("__AXES__", json.dumps(AXES))
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Write the catalogue's artefacts from its entries: README.md and "
        "docs/radar/index.html."
    )
    ap.add_argument(
        "--public",
        action="store_true",
        help="also write the public projection, PUBLIC.md and docs/radar/public.html, "
        'from the entries marked visibility = "public"',
    )
    ap.add_argument(
        "--data-root",
        help="the radar data root, instead of searching upwards for radar.toml. For CI "
        "and nothing else; it still has to name a directory that carries the "
        "marker",
    )
    return ap


def main() -> None:
    # Parsed before anything is resolved or written. Without a parser, `--help` fell
    # through to a full render that rewrote the catalogue's tracked files. `--data-root`
    # is still read from argv by the resolver (radar_lib._flag_value); declaring it here
    # is what lets the parser accept it and list it in the help.
    args = build_parser().parse_args()
    root = begin_command()
    marker = load_marker(root)
    # The title is a DATUM the catalogue carries, not this engine's identity - an engine
    # anyone installs must not title a stranger's radar with the name of the catalogue it
    # grew up in.
    title = str(marker.get("title") or "radar")
    boundary_note = str((marker.get("render") or {}).get("boundary_note") or DEFAULT_BOUNDARY_NOTE)
    public_mode = args.public
    # include_local=False is load-bearing, not tidiness. README.md and docs/radar/*.html
    # are tracked; a tools.local/ entry rendered into them would be committed and pushed,
    # which is precisely what putting it in a gitignored directory was meant to prevent.
    # If a local entry ever needs to appear somewhere, that somewhere must be untracked.
    tools = load_tools(include_local=False)
    local_n = len(load_tools()) - len(tools)
    today = datetime.date.today().isoformat()
    counts = {r: sum(1 for t in tools if t["ring"] == r) for r in RINGS}
    head = [
        f"# {title}",
        "",
        "Generated by `radar render` - do not hand-edit. Source of truth: `tools/*.toml`.",
        f"\n{today} - {len(tools)} tools - "
        + " - ".join(f"{r} {c}" for r, c in counts.items() if c),
        "",
        "Workflow: `radar snapshot` refreshes metrics -> `radar gate` applies the "
        "backing gate (staleness, license drift, astroturf detector) -> ring transitions require "
        "a dated history entry with evidence -> `radar render` regenerates this file and "
        "`docs/radar/index.html`.",
        "",
        boundary_note,
    ]
    body = artifact_view(tools) + ring_tables(tools, public=False) + transitions(tools)
    (root / "README.md").write_text("\n".join(head + body) + "\n", encoding="utf-8")
    radar_page(tools, root / "docs" / "radar" / "index.html", title)

    if public_mode:
        pub = [t for t in tools if t.get("visibility") == "public"]
        phead = [
            f"# {title} (public projection)",
            "",
            f"{today} - {len(pub)} tools. Factual adoption state only; see repo history for evidence.",
        ]
        pbody = artifact_view(pub) + ring_tables(pub, public=True)
        (root / "PUBLIC.md").write_text("\n".join(phead + pbody) + "\n", encoding="utf-8")
        radar_page(pub, root / "docs" / "radar" / "public.html", title)
    print(
        f"rendered README.md ({len(tools)} tools)"
        + (" + public projection" if public_mode else "")
        + (
            f" - {local_n} machine-local entr{'y' if local_n == 1 else 'ies'} "
            "deliberately excluded from tracked output"
            if local_n
            else ""
        )
    )


if __name__ == "__main__":
    main()
