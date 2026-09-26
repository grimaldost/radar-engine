# Contributing

This is a small tool with one maintainer, so "contributing" here mostly means *what the
mechanisms hold a change to, and why each one is where it is*. Everything below is
enforceable; nothing below is taste.

## Set up

```
git clone <this-repo> && cd stack-radar
uv sync
uv run radar --help
```

The engine has **no runtime dependencies** — standard library only, Python 3.11 and up — and
that is a constraint rather than an accident. These commands run in environments where
installing anything is a policy question, so a runtime dependency would be a new
precondition for the whole exercise. It is also what makes the fallback work: where a wheel
cannot be installed at all, `PYTHONPATH=<checkout>/src python -m stack_radar.cli` is a
complete installation.

The `dev` dependency group carries `pytest`, `ruff` and `ty`. Those are for developing the
engine and are never runtime requirements. Adding a runtime dependency is a decision to
argue for, not a convenience to reach for.

## The checks, in this order

Every change has to pass this list. Run it locally; it is the same list continuous
integration runs, on Linux and on Windows.

```
uv lock --check
uv sync
uv run ruff format --check .
uv run ruff check .
uv run ty check src
uv run python -m pytest -q
```

`uv lock --check` goes **first** and cannot be moved. No test can guard lock staleness,
because `uv run` re-locks before pytest could read the file — by the time anything else runs,
the thing being checked has already been rewritten.

Two more that judge this repository rather than a catalogue, and which need no data root:

```
uv run radar check-version-sites
uv run radar changelog-gate <base-ref>
```

The suite is hermetic: the tests that exercise commands end to end build a miniature
catalogue in `tmp_path` and run against that. Nothing in it reads the machine's real
`~/.claude` or writes outside the temporary directory.

### The commit lane

`.githooks/` carries a tracked `pre-commit` and `commit-msg` pair, armed per clone with:

```
git config core.hooksPath .githooks
```

They are tracked files rather than generated ones so that a fresh clone arms the lane with
one command. They deliberately do **not** go through `pre-commit install`: that writes hooks
which shell out to the bare `pre-commit` executable, and a machine that enforces binary
reputation can refuse to run the shim — leaving `.git/hooks/` looking armed while nothing
runs, which is worse than no hook, because the absence is silent. The tracked scripts invoke
`uv run python -m pre_commit` instead: the project's own pinned interpreter, no shim, and a
hook a reviewer can read.

**As of this commit the lane is armed on paper only.** The hooks reference a
`.pre-commit-config.yaml` this repository does not yet carry, and `pre-commit` is not in the
`dev` group, so arming `core.hooksPath` today produces an error rather than a check. Run the
list above by hand until both land. This is written down rather than left to be discovered,
because a hook that fails to run looks exactly like a hook that passed.

## Commits

Conventional Commits: `feat fix docs style refactor perf test chore build ci release`, an
imperative subject, one concern per commit.

**No AI-attribution trailers.** Not `Co-Authored-By` lines naming a model, not "generated
with" footers, not anywhere in the message.

**Red proofs go in the body.** Every check that ships carries recorded proof that it can
fail: the new test run against the pre-change code, with the failing assertion pasted under a
`Red proof:` heading. A verifier that has never been red has not been tested.

The suite is built for that. `RADAR_ENGINE_SRC` points the tests at a different `src/` tree,
so extracting one from an earlier commit into a temporary directory and running the same
tests against it is the whole procedure:

```
git worktree add <tmp> <base-commit>
RADAR_ENGINE_SRC=<tmp>/src uv run python -m pytest -q tests/<the-new-test>.py
```

**The verifier is not edited in the change it judges.** A fixture or an oracle moves in its
own commit, or in the commit that perturbs its source when that is unavoidable — with the
reason in the message either way.

## Recording a change

`CHANGELOG.md`, [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), headings exactly
`## [X.Y.Z] - YYYY-MM-DD`. That grammar is load-bearing rather than cosmetic: the heading is
one of the three version sites `radar check-version-sites` compares, so a heading written
some other way is a version site that has stopped existing.

`src/` is the watched surface. `radar changelog-gate` judges **every commit in a pushed range
on its own**: a commit whose own diff touches `src/` must either be recorded by the pull
request's `CHANGELOG.md` diff or carry a `Changelog: none (<reason>)` trailer in its *own*
message. A trailer on a neighbouring commit does not cover it, and a bare `Changelog: none`
does not satisfy it — the reason is the reviewable part.

A record is an **added line** whose whitespace-collapsed form the same diff does not also
remove. Touching the file, reflowing it, reordering bullets, converting line endings or
deleting it outright all record nothing.

Three files are **contract surfaces** — `src/stack_radar/radar_lib.py` (the entry schema and
the closed `[telemetry]` key set), `src/stack_radar/field_report.py` (the report document
another reader parses) and `src/stack_radar/gate.py` (the exit code a ritual branches on). A
change to any of them that alters what a consumer sees is marked `(consumer-affecting)` in
its changelog entry.

Argue the bump class **in prose**, inside the entry ("minor and not patch, because…").
Nothing judges major/minor/patch mechanically; the gate checks presence and coherence, and
the judgement stays with the author.

## Releasing

Three version sites have to agree: `pyproject.toml [project].version`, `CHANGELOG.md`'s
newest released heading, and `stack_radar.__version__`. `radar check-version-sites` compares
them, and so does the test suite.

A release is a metadata-only `release: X.Y.Z` commit — the changelog roll plus the version
bump, never a feature, so `git bisect` keeps working — and an **annotated tag on that
commit**:

```
git tag -a v0.2.0 <release-commit> -m "stack-radar 0.2.0"
```

Annotated, not lightweight: a lightweight tag records no tagger date, so a tag laid at the
wrong commit cannot say when it was laid. A published tag is never re-pointed; a tag error is
corrected forward, with a new release.

## What a change should not do

- **Name a particular catalogue, or the tools in one.** One engine serves any number of
  catalogues and knows none of them by name. A name that belongs to somebody's stack belongs
  in their `radar.toml`, their entries or their profiles — never in this repository, in code
  or in prose.
- **Write a real machine path.** No `C:/Users/<an-account>` in an example, ever. Write the
  placeholder a profile fills, which is what the example actually means.
  `tests/test_docs.py` checks the documents for this, and `radar gate` checks a whole tree.
- **Reach for `Path.home()`.** Exactly one module may find the home directory —
  `bootstrap.py`, because that is its job. Everywhere else the machine path comes from the
  environment profile's `[paths]`, which is what lets one entry work on several machines.
- **Make a check quieter.** A check that cannot run says so and names itself; it never counts
  as a pass. "0 findings" out of a scan that read nothing is the exact wording of a clean
  scan, and that collision is the failure mode most of this code is shaped around.
