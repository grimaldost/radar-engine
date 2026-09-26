# Concepts

The vocabulary, and the reasoning behind each word. Nothing here assumes you have used a
tech radar before, or that you have anything installed yet.

Two examples run through the whole document, both real and both boring on purpose:
[`ruff`](https://github.com/astral-sh/ruff), a Python linter and formatter, and
[`just`](https://github.com/casey/just), a command runner. One you have probably already
committed to; the other you have probably been meaning to try. Those are the two states most
of a real catalogue is in.

## The engine and the catalogue are two different things

`stack-radar` is the **engine**: a command, installed once, that holds no data.

Your **catalogue** is a separate directory — a git repository of your own — holding one TOML
file per tool, your environment profiles, and the artefacts the engine renders from them. A
directory becomes a catalogue by carrying a `radar.toml` at its top. That file is called the
marker, and the engine finds a catalogue by walking up from the working directory until it
meets one, the way `git` finds `.git`.

The split is not tidiness. Three things follow from it, and each one is the reason for a
design decision further down:

- **The engine knows no catalogue by name.** One installed engine serves as many catalogues
  as you have. Nothing in the engine is specialised to yours.
- **Your catalogue is yours.** It holds the names of your tools, your machines' paths, and
  your reasons for dropping things. None of that is in the engine, so upgrading the engine
  cannot leak it and publishing the engine cannot publish it.
- **A version range is checkable.** `radar.toml` declares `requires_framework`, the range of
  engine versions the catalogue can be read by. An old engine reading a new catalogue fails
  loudly instead of silently ignoring a field it does not know about.

`radar init` creates a catalogue. Two verbs are the exception to all of this —
`radar changelog-gate` and `radar check-version-sites` judge whatever repository they are run
from and need no catalogue at all.

## The control plane, and the invariant that makes it reversible

Your catalogue is a **control plane**. The thing it governs — `~/.claude`, its plugins and
skills, the MCP servers you have mounted, the repositories on your machine — is the **data
plane**.

Knowledge flows one way. The catalogue reads and writes the stack; **no file in the stack may
ever reference the catalogue.** Not a path in `settings.json`, not an import, not a hook.

That is one rule, and it buys one property: if the catalogue directory were deleted right
now, nothing would break. Every install stays installed. Every plugin still loads. You would
lose the record of how the stack got to be the way it is, and not the stack. Adopting a
radar is therefore reversible, which is the only honest way to ask someone to adopt one.

A rule this easy to break by accident is worth checking mechanically, so `radar gate` greps
the governed tree for the catalogue's own absolute path and for the names it declares in
`radar.toml [invariant].needles`, and reports how many files it actually read. It says
`holds` only over a scan it can name the size of.

## Rings — how committed you are

Five rings. The order is not a ladder anything climbs automatically.

| ring | what it claims |
|---|---|
| `own` | you or your team wrote it — you are the people who can land a fix |
| `adopt` | the default choice for its problem; present and in use |
| `pilot` | a running experiment with a deadline |
| `observe` | interesting, not committed to; costs nothing and claims nothing |
| `discard` | evaluated and rejected, or dropped after use |

`ruff` is `adopt`: it is the linter in every Python repository here, and that is a fact about
the machine, not a plan. `just` is `observe` until it has actually been tried, and moving it
to `pilot` is a decision with a date on it.

**A ring is a claim about observed state, not about intent.** `adopt` and `pilot` require
presence — an install probe that passes, a real dependency in a project, a mounted server. If
a tool is not actually there, the honest ring is `observe`. This is the rule that stops a
radar from decaying into a wishlist, and it is enforced: `own` and `adopt` entries whose use
a session transcript could see must declare how that use is recognised (see *Telemetry*
below), or the gate fails them.

**Every ring change carries a dated history block** with a reason and evidence:

```toml
[[history]]
date = "2026-09-13"
ring = "pilot"
reason = "Three repositories have grown a Makefile used only as a task runner"
evidence = "make targets in 3 repos, none of which compile anything"
```

Two rules the history exists to make possible:

- Promoting an experiment needs a **measurement or evidence of real use**, never an
  impression. Demoting one needs the fact that caused it.
- An experiment must state, **before** any data exists, what would end it. That is the
  `[pilot_exit]` block: how many days until review, what would adopt it, what would decline
  it. Missing it is a failure, not a warning, and the reason is unflattering: whoever reads
  the numbers after choosing what to look for will find what they wanted. Writing the
  criteria first costs a minute. A demotion does not erase them — letting an experiment come
  back with newer, slacker criteria is pre-registration laundering.

## Axes — which problem it solves

`axis` is the one-word answer to "what is this for". The set is closed, because a free-text
field here becomes fifty near-synonyms and nothing can be grouped:

`cc-ecosystem` · `token-economy` · `codebase-understanding` · `orchestration` ·
`eval-observability` · `memory-knowledge` · `python-engineering` · `data-lineage` ·
`mcp-servers` · `multi-model`

Both `ruff` and `just` are `python-engineering`. An axis groups things that compete: two
entries on the same axis at `adopt` is a question worth answering.

## Artifact — what you install, and where it runs

`artifact` is the dimension an axis cannot carry, and the one that most often decides whether
you can have a tool at all.

| artifact | what it commits you to |
|---|---|
| `cc-plugin` | loads into the agent: skills, agents, commands, hooks |
| `mcp-server` | a tool surface **inside the agent's context** — it costs tokens every session |
| `cc-lsp` | a language server driven natively by the harness; no tool surface |
| `cli` | a command in a shell or in CI; no context cost, easy to audit |
| `python-lib` | imported by project code, so it becomes a runtime dependency |
| `service` | a server and a UI somebody has to operate, authenticate and patch |
| `standard` | nothing to install — a format to target |
| `corpus` | authored knowledge, not executable |
| `unknown` | never established; rejected on evidence before its nature mattered |

`ruff` and `just` are both `cli`, and that is most of why they are cheap: a CLI costs nothing
in an agent's context window and can be read by anyone auditing the machine. An `mcp-server`
on the same axis would be a different commitment entirely — same problem solved, tokens spent
on every single session whether it is used or not, and a locked-down environment may simply
refuse it.

`unknown` is honest by design. It means: we did not establish what this is, because it failed
on evidence before that mattered.

## An entry, whole

Eight fields are required — `name`, `repo`, `axis`, `ring`, `license`, `visibility`, `note`,
`artifact` — and the rest are opt-in.

```toml
name = "just"
repo = "https://github.com/casey/just"
axis = "python-engineering"
artifact = "cli"
ring = "observe"
license = "CC0-1.0"
visibility = "public"
note = "Language-agnostic command runner; a Make alternative that is only a task runner"

[[history]]
date = "2026-09-13"
ring = "observe"
reason = "Recurring friction: Makefiles used as task runners in repos that compile nothing"
evidence = "3 repositories with a phony-target-only Makefile"
```

`visibility` decides whether an entry appears in the projection `radar render --public`
writes — which is how a private catalogue can share its opinions about third-party tools
without publishing anything about itself. A public entry must give `repo` as a URL, because
the projection writes it as a link; a local path there would publish that path.

`eval_status` records how an entry's value was established: `unmeasured` by default,
`eval-pending` while a measurement is scheduled but not yet made, or
`measured:<what was measured>`. It exists so that "we like it" and "we measured it" cannot be
written the same way.

`[install]` says how to converge the tool, and its `kind` — `uv-tool`, `npm-tool`,
`claude-plugin`, `mcp-plugin`, `mcp-server`, `pip`, `repo`, `manual` or `none` — decides what
`radar apply` may do. Everything except `manual` and `none` needs a `check` probe that is
idempotent and exits 0 when the tool is present. `manual` prints instructions and executes
nothing, which is the right answer for anything an environment's policy has to approve first.

`[version]` sets what "current enough" means for this entry: `latest` (warn when behind the
registry), `pin` (fail on any deviation — for a tool whose behaviour you measured at one
version), `floor` (fail below a minimum, silent above), or `any` (never compared).

## Telemetry — evidence that costs nothing to collect

A ring claims observed state, so something has to be able to check the claim. Claude Code's
session transcripts already record every tool call, so `radar field` mines them **after the
fact**. There is no hook to install, no wrapper, no prompt: a tool you start watching today
already has a history, and watching one costs it nothing.

An entry declares how its use is recognised:

```toml
[telemetry]
match_command = ["ruff"]      # the executable heading a shell command
since = "2026-09-13"
```

There are three matcher shapes — `match` for MCP tool names, `match_skill` for skill ids,
`match_command` for a CLI — and a parallel set (`opportunity_match`, and so on) pointing at a
different question: not "was it used" but "was there an occasion where it would have
applied". Without a denominator that is narrower than "every session on this machine", a
usage count means very little.

The `[telemetry]` key set is **closed**. A mistyped matcher name is not a TOML error; it is a
silent zero, and a zero reads as evidence. So an unrecognised key is reported rather than
ignored.

Some artifacts leave no trace a session can see. For those, record the absence and why —
which is a better answer than a matcher that can never match.

## The backing gate

`radar gate` is the thing that makes the rest of it more than a document. It exits 1 on any
FAIL. In one run it checks:

- **Schema** — every entry has the eight required fields, with values from the closed sets.
- **Evidence** — every ring change has a dated history block with a reason.
- **Exit criteria** — every experiment has pre-registered ones.
- **Telemetry** — every `own`/`adopt` entry whose use is observable declares a matcher, or
  records why none can exist.
- **Licences** — drift against what upstream currently states, with copyleft and
  source-available terms called out rather than buried.
- **Staleness** — an entry whose upstream has not moved in 180 days.
- **An astroturf signature** — many stars, almost no downloads. Not proof of anything;
  a reason to look.
- **Version sites** — the places a repository writes its own version agree with each other.
- **The invariant** — the governed stack still does not reference the catalogue.

Two habits are worth copying. The gate **names what it could not check** instead of counting
a skipped check as a pass: a scan that read nothing prints the same "0 references" as a scan
that read everything, so the distinction has to be in the output. And it reports the size of
what it read, so a green line can be argued with.

`radar gate --no-data-plane` answers the way a CI runner does — no `~/.claude`, no governed
worktrees checked out — and says which checks that cost it.

## Where to go next

- [new-environment.md](new-environment.md) — bringing a catalogue up on a machine that has
  never run it, including the decisions no script can make for you.
- `radar <verb> --help` — every verb documents its own options and exit codes.
