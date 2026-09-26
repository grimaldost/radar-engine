# Bringing a catalogue up on a new machine

You have a catalogue in git and a machine that has never run it — a second workstation, a
work laptop, a locked-down box behind a proxy. This is the walk-through, and its shape
matters: the steps a command can do are separated from the four decisions no command can
make for you.

Nothing about the machine is assumed. A committed environment profile ships with paths that
are true of the machine that wrote it and false of this one, and **a wrong path that looks
plausible is worse than a missing one** — so the first step discovers rather than trusts.

## Before you start

You need `git`, Python 3.11 or newer, and the engine:

```
uv tool install git+https://github.com/grimaldost/radar-engine@v0.2.0
```

Then get the catalogue onto the machine. This part is not agent work:

```
git clone <your-catalogue-url> my-radar && cd my-radar
```

Air-gapped instead? Carry a bundle and `git clone my-radar.bundle my-radar`. Append-only
dated reports are what make bundle merges conflict-free, which is why that route is supported
at all.

Everything below runs from inside the catalogue. Pick a short name for this environment —
`corp`, `laptop`, `<ENV>` in what follows — and use it consistently: it is the key for the
profile, the convergence scope, and every report.

## Step 1 — discover the machine (read-only)

```
radar bootstrap --env <ENV>
```

It reports the platform, the real paths, which commands exist, which plugins are already
installed, which MCP servers are already mounted, and how the committed profile's declared
paths differ from this machine's actual ones. **It writes nothing without `--write`.**

If no profile exists for `<ENV>`, it prints a starter one instead. Read it, then create it:

```
radar bootstrap --env <ENV> --write
```

That writes `environments/<ENV>.local.toml`, which is **gitignored**. The intent matters more
than the command: machine-specific paths are true of one workstation and false of every
other, so they must never enter the committed profile — a machine that pulled them would
break. If you find yourself wanting to edit the committed `environments/<ENV>.toml` to fix a
path, that is the signal that you want the overlay instead.

A path under the home directory can be written with a leading `~` (`~/.claude`) in either
file. Every reader of a profile expands it at run time, so the profile says where the path
is without naming the account.

## Step 2 — settle the policy

This is the part no command can do. Open `environments/<ENV>.toml` and decide four things.
Do not guess on the flags: both failure directions are real. A wrongly permissive flag can
push data or install software where policy forbids it; a wrongly restrictive one only costs
you a manual step.

1. **`rings`** — which rings this environment converges. A new environment starts
   `["own", "adopt"]`. `pilot` is deliberately absent: an experiment does not belong on a
   machine where a broken tool costs someone else their afternoon. Add it only if this
   machine is yours to break.
2. **`flags.allow_external_mcp`** — may this machine run MCP servers that reach outside the
   network boundary? Default `false`.
3. **`flags.allow_network_install`** — may `radar apply` install from the network? Default
   `false`; when false it prints manual instructions instead of running anything.
4. **`sync`** — `"git-remote"` if this machine can reach the catalogue's remote,
   `"git-bundle"` for hand-carry.

**Never put a secret in any file here.** `require_env` holds the *names* of required
environment variables and nothing else; the engine checks that they are present and never
reads or prints their contents.

One machine constraint worth discovering now rather than later: an operating system that
enforces binary reputation can refuse to run an unsigned executable, which makes an
`artifact = "cli"` entry uninstallable here even with `allow_network_install = true`. The
flags govern policy; that governs physics. Record it in the profile as a comment when you
meet it, because the symptom — a probe that reports UNKNOWN — looks like a broken probe.

## Step 3 — declare this environment's own tools, if it has any

An environment often has tools that exist only in it: an in-house library, a server somebody
built internally. Two mechanisms, and the choice is yours per tool, because the costs differ:

- `tools/<name>.toml` with a top-level `environments = ["<ENV>"]` — travels in git, so it is
  written once and appears on every machine in that environment automatically. **Cost: the
  tool's name lives in the repository.**
- `tools.local/<name>.toml` — gitignored, never travels. **Cost: written again on each
  machine.**

If a name itself is confidential, the second is the only correct answer, and `radar gate`
enforces the consequence: no tracked file may contain a name declared only in `tools.local/`.

Two similar fields that are **not** the same thing, because conflating them is the easy
mistake: the top-level `environments` says which environments a tool *exists* in;
`[install].environments` says where `radar apply` should *converge* it. The second must be a
subset of the first.

Write entries with `radar add`, which validates before the file lands.

### Homes that are not this machine's

`radar redact-backfill` and `radar sync-feedback` map this machine's home directory to `~`
so no tracked file names the account. A report written on a *different* machine and synced
in carries that machine's home, which this one cannot discover. Declare those in
`redact.local.toml` at the data root:

```toml
# Homes belonging to other machines whose reports reach this catalogue.
homes = ["C:/Users/<account>", "/home/<account>"]
```

Gitignored, for the same reason `tools.local/` is: the account name is precisely what the
redaction exists to withhold, so a tracked declaration would publish it in order to say it
must not be published. It is written once per machine that ingests those reports. A path is
read in the flavour it is written in, so a Windows home declared on a POSIX machine still
matches both of its spellings. No file, or no entries, and nothing changes.

## Step 4 — converge

```
radar apply --env <ENV> --plan
```

This writes **nothing**. Read the plan before applying it. Then:

```
radar apply --env <ENV> --apply --yes
```

Two properties to preserve rather than work around:

- `radar apply` never executes a **removal**. It proposes them and leaves them to a human.
- A probe it cannot run reports **UNKNOWN** rather than "absent", so nothing is installed or
  demoted on the strength of a broken check. If a probe comes back UNKNOWN, investigate the
  probe; do not force the outcome.

Entries with `install.kind = "manual"` print instructions instead of running. That is a
design decision, not a failure to fix.

## Step 5 — verify, and read the result honestly

```
radar gate                                  # expect 0 FAIL
radar reconcile --env <ENV>                 # declared rings vs observed reality
radar apply --env <ENV> --check             # machine vs profile
radar versions --env <ENV>                  # installed versions vs upstream
radar render-feedback-targets --env <ENV>
radar sync-feedback --env <ENV> --check
```

Every one of these is idempotent and safe to re-run.

`radar reconcile` will probably propose demotions, and you should not apply them. On a
machine where the stack was installed twenty minutes ago, "no observed evidence" mostly means
"no sessions have happened yet" — the difference between absence of evidence and evidence of
absence, which this environment has not existed long enough to tell apart. Report the
proposals and leave them.

Note what `radar gate` says it **skipped**. On a fresh machine some checks have nothing to
read yet, and the output names them individually rather than folding them into the pass.

## When it is done

The environment is up when `radar gate` reports 0 FAIL and `radar apply --check` shows no
pending action other than the deliberately manual entries. From then on the machine needs
nothing daily: feedback reports and field telemetry accumulate on their own, and the review
ritual runs weekly or monthly.

## Appendix — handing this to an agent

The walk-through above works as a hand-off to a fresh Claude Code session, and that is how it
was originally written. Paste the block below into a session running inside the freshly
cloned catalogue, replacing `<ENV>`.

It is written for a session with **nothing**: no plugins, no skills, no `CLAUDE.md`, no
memory of anything before it. Everything it needs is either in the block or in a file it is
told to open. It deliberately names no in-house tool, since the block may end up committed.

```
You are continuing a prior session. The state you need is below. Pick up from here — the user will direct next steps once you acknowledge the hand-off.

You are in a freshly cloned radar catalogue on a machine that has never run it. Your job: discover what this machine actually is, settle the policy questions a command cannot settle, converge the stack, and verify. The environment name for this machine is `<ENV>`.

The catalogue is a CONTROL PLANE. It observes a tool stack, decides, applies, and then gets out of the way. The stack it governs — Claude Code plugins, skills, MCP servers, `settings.json`, `CLAUDE.md`, the user's own repositories — is the DATA PLANE. Knowledge flows ONE way: the catalogue reads and writes the stack; no file in the stack may reference the catalogue. If the catalogue were deleted, the stack must freeze in its current state and keep working. That is the load-bearing invariant, and `radar gate` proves it mechanically by grep. Never write a path to the catalogue into any file under the user's `.claude` directory or into any tool's working tree.

Nothing about this environment is assumed. The committed profile ships with paths that are true of another machine, because a wrong path that looks plausible is worse than a missing one.

Read `docs/concepts.md` from the engine's documentation before acting: rings, axes, artifact kinds, and what the gate checks. Requirements on the machine: `git`, Python 3.11+, and the `claude` CLI.

Step 1 — discover, read-only:

radar bootstrap --env <ENV>

It writes nothing without `--write`. If no profile exists for `<ENV>` it prints a starter one; review it, then write it with `--write` and commit. The overlay it writes, `environments/<ENV>.local.toml`, is gitignored on purpose: machine-specific paths must never enter the committed profile.

Step 2 — settle the policy. This is the part no command can do. Open `environments/<ENV>.toml` and decide four things; ask the user for any you cannot determine from the machine or from local rules, and do NOT guess on the flags, because both failure directions are real.

1. `rings` — which rings this environment converges. A new environment starts ["own", "adopt"]. `pilot` is deliberately absent: an experiment does not belong on a machine where a broken tool costs someone else's time.
2. `flags.allow_external_mcp` — may this machine run MCP servers that reach outside the network boundary? Default false.
3. `flags.allow_network_install` — may `radar apply` install from the network? Default false; when false it prints manual instructions instead of running anything.
4. `sync` — "git-remote" if this machine can reach the catalogue's remote, "git-bundle" for hand-carry.

Never put a secret in any file here. `require_env` holds the NAMES of required environment variables only.

Step 3 — declare this environment's own tools, if it has any. Two mechanisms, and the choice is the user's per tool: a tracked `tools/<name>.toml` with `environments = ["<ENV>"]` travels in git but puts the tool's name in the repository; a gitignored `tools.local/<name>.toml` never travels but is written again on each machine. Ask which applies; if the user has not raised it, do not invent entries. Note that top-level `environments` says which environments a tool EXISTS in, while `[install].environments` says where `radar apply` should CONVERGE it, and the second must be a subset of the first. Write entries with `radar add`, which validates before the file lands.

Step 4 — converge:

radar apply --env <ENV> --plan

This writes NOTHING. Read the plan out to the user and get approval before applying. Then `radar apply --env <ENV> --apply --yes`. Two properties to preserve rather than work around: it never executes a REMOVAL, and a probe it cannot run reports UNKNOWN rather than "absent". If a probe comes back UNKNOWN, investigate the probe; do not force the outcome. Entries with `install.kind = "manual"` print instructions by design.

Step 5 — verify, and report honestly:

radar gate
radar reconcile --env <ENV>
radar apply --env <ENV> --check
radar render-feedback-targets --env <ENV>
radar sync-feedback --env <ENV> --check

All idempotent. `radar reconcile` will likely propose demotions — do NOT apply them. On a machine where the stack has only just been installed, "no observed evidence" mostly means "no sessions have happened yet", which is the difference between absence of evidence and evidence of absence. Report the proposals and leave them. A ring may only claim what is observably true: `adopt` and `pilot` require presence — a passing install probe, a real project dependency, or a mounted server. If something is not present, the honest ring is `observe`, which costs nothing and claims nothing.

Suggested opening: run step 1 and show the user the discovery report before changing anything. Then walk through the four policy decisions in step 2, one at a time.
```
