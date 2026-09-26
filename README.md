# stack-radar

A tech radar that governs a Claude Code stack — which plugins, skills, MCP servers and
command-line tools you have adopted, on what evidence, and what would make you drop one —
plus a gate that refuses the entries that only claim to be true.

It is a **control plane**. It reads the stack, decides, converges it, and then gets out of
the way. The stack itself — `~/.claude`, its plugins and skills, the MCP servers you have
mounted, your own repositories — never learns this tool exists. Delete the catalogue and
nothing breaks: every install stays installed, and you lose the record of how it got there,
not the thing itself.

## Is this for you

**Yes, if** you run Claude Code seriously enough that the question "why is this plugin
installed, and what would make me remove it?" has stopped having an obvious answer; if you
work across more than one machine and they have drifted; or if you want a tool decision to
have to cite something.

**Partly, if** you want a tech radar for a stack that has nothing to do with Claude Code.
The catalogue format, the backing gate, `radar render` and `radar snapshot` are indifferent
to what a tool is, and a catalogue of nothing but CLIs and Python libraries works exactly as
described. But three verbs read `~/.claude` directly — `radar reconcile`, `radar versions`
and `radar field` — so half of what makes this more than a spreadsheet is Claude-Code
shaped. This README would be lying if it opened by calling it a general-purpose radar.

**No, if** you want a dashboard. There is no server, no web app and no database: the
catalogue is a directory of TOML files in git, and every artefact is regenerated from them.

## What you get

- **A catalogue.** One TOML file per tool, in git, with a dated history. A ring change
  needs a reason and evidence, or the gate fails.
- **A gate.** Schema, licence drift, staleness, pre-registered exit criteria for
  experiments, telemetry matchers, version-site agreement, and a scan proving the governed
  stack still does not reference the catalogue.
- **Field telemetry with no instrumentation.** Claude Code's transcripts already record
  every tool call. `radar field` mines them after the fact, so a tool you started watching
  today still has a history.
- **Convergence.** `radar apply` plans first, installs only on request, and never executes
  a removal — it proposes them and leaves them to you.

See [docs/concepts.md](docs/concepts.md) for the vocabulary: rings, axes, artifact kinds,
the backing gate, and why the engine and the catalogue are separate things.

## Install

Requires Python 3.11 or newer, and `git`. The engine itself has **no runtime dependencies**
— everything it uses is in the standard library — which is deliberate: a command you run
inside a locked-down environment should not need an installation policy decision first.

```
uv tool install git+https://github.com/grimaldost/radar-engine@v0.2.0
radar --version
```

The package is not on PyPI yet, so it installs from this repository, at a release tag.
`pipx install git+https://github.com/grimaldost/radar-engine@v0.2.0` works too. Where you
may not install anything at all, a checkout is already a complete installation:

```
PYTHONPATH=<checkout>/src python -m stack_radar.cli --help
```

## Quickstart

Five commands, from nothing to a catalogue that renders. Run them somewhere new — the
catalogue is its own directory, separate from the engine and from anything it governs.

```
radar init my-radar
cd my-radar
git init && git add -A && git commit -m 'chore: the catalogue'

radar add ruff \
    --repo https://github.com/astral-sh/ruff \
    --axis python-engineering --ring adopt --artifact cli \
    --license MIT --visibility public --registry pypi:ruff \
    --note 'Linter and formatter; the incumbent in every Python repository here' \
    --reason 'Already the linter in every project; the entry records a state, not a decision' \
    --evidence '11 repositories carry a [tool.ruff] section' \
    --telemetry-match-command ruff

radar gate      # expect 0 FAIL
radar render    # writes README.md and docs/radar/index.html
```

`radar init` writes the marker (`radar.toml`), an environment profile carrying the paths it
**discovered on this machine** rather than placeholders to fill in, an empty `tools/`, a
`CHANGELOG.md` and `pyproject.toml` giving the new root two agreeing version sites, the
commit lane, and a CI workflow. It prints the remaining steps, including the two that git on
Windows will not do for you.

`radar add` validates the entry **before** the file lands, and refuses not only what the
schema rejects but what the gate would fail on the very next command — an experiment with no
pre-registered exit criteria, an adopted tool whose usage a session could see and which
declares no telemetry matcher.

`radar gate` reads the governed stack, so its first run is not instant: on the machine this
was written on it scanned 18,795 files in 144 seconds. `radar gate --no-data-plane` answers
the way a CI runner does and skips that half — naming what it skipped, rather than reporting
a scan of nothing as a pass.

## The verbs

`radar <verb> --help` for a verb's own options.

| verb | what it does |
|---|---|
| `radar init` | create a catalogue: the marker, a profile, the commit lane and CI |
| `radar add` | write one validated entry, with its first dated history block |
| `radar gate` | judge the catalogue and the governed stack; exit 1 on any FAIL |
| `radar render` | write the catalogue's artefacts from its entries |
| `radar apply` | converge a machine towards what an environment declares |
| `radar field` | field telemetry for an entry, from session transcripts |
| `radar reconcile` | compare what is installed against what the catalogue claims |
| `radar snapshot` | record today's upstream licence and release state |
| `radar versions` | probe installed versions against what upstream publishes |
| `radar sync-feedback` | ingest pending feedback reports into the catalogue's archive |
| `radar render-feedback-targets` | write the feedback-target registry into the stack |
| `radar redact-backfill` | map scoped names and the home directory out of tracked content |
| `radar bootstrap` | survey a machine and propose an environment profile |
| `radar changelog-gate` | fail a change that moves a repository's code unrecorded |
| `radar check-version-sites` | the version sites of a repository agree |

The last two judge whatever repository they are run from rather than a catalogue, which is
how this repository runs them on itself. Everything else needs a data root: the engine finds
one by walking up from the working directory until it meets a `radar.toml`, the way `git`
finds `.git`. `--data-root` and `RADAR_DATA_ROOT` name one explicitly, for CI.

## Documents

- [docs/concepts.md](docs/concepts.md) — rings, axes, artifact kinds, the backing gate, and
  the split between engine and catalogue.
- [docs/new-environment.md](docs/new-environment.md) — bringing a catalogue up on a machine
  that has never run it.
- [CONTRIBUTING.md](CONTRIBUTING.md) — what the checks hold a change to, and why each one is
  where it is.
- [CHANGELOG.md](CHANGELOG.md) — what moved, and when.

## Licence

[MIT](LICENSE).
