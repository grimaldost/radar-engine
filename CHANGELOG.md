# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

A heading is `## [X.Y.Z] - YYYY-MM-DD`, and that grammar is load-bearing rather than
cosmetic: it is the version site `radar check-version-sites` compares against
`pyproject.toml` and `stack_radar.__version__`, so a heading written some other way is a
version site that has stopped existing.

## [Unreleased]

## [0.2.0] - 2026-09-26

The first public release. The repository starts its public history here, with one commit
holding this tree; the history before it stays private, because it predates the separation
from the catalogue this engine was extracted from.

### Added

- `tests/test_environment_home.py` holds the starter profile that `radar bootstrap --write`
  writes to the reading side: a bare home becomes a bare `~`, a path outside the home and
  a key nothing was found for are written as they were, the profile loads back to the
  discovered paths, and a second run reports no drift against the file the first one
  wrote. These cases lived only in the copy of this module that the catalogue this engine
  was extracted from still carried; that copy is being removed, and this is where they
  land first. Tests only, so no version moves.
- **A catalogue may declare home directories that are not this machine's.** A report
  written on another workstation and synced in carries that machine's home, and the
  redaction asks `bootstrap.home_dir()`, which answers with the account running the
  command — so the other homes survived every backfill. `redact.extra_homes` reads them
  from `redact.local.toml` at the data root (`homes = [...]`), and `redact.redact_homes`
  puts each one through the same `redact_home` as the machine's own: path forms, both
  slash styles, the JSON-escaped one and the session-slug form. `radar redact-backfill`
  and `radar sync-feedback` apply them, so `--check` exits 1 while a declared home is
  still in a tracked file. A declared path is read in the FLAVOUR IT IS WRITTEN IN rather
  than the one the machine runs, or a Windows home declared on a POSIX runner would lose
  the backslash spelling that most of a mined corpus uses. No file, no `homes`, an empty
  list or blank entries: nothing changes and nothing is reported; a file that does not
  parse raises, because a declaration that could not be read must not look like one that
  declared nothing. The file is gitignored (`gitignore.tmpl`, so `radar init` writes the
  rule) for the same reason `tools.local/` is — the account name is what the redaction
  exists to withhold, and a tracked declaration would publish it in order to say it must
  not be published. A file of its own rather than an entry under `tools.local/`, because
  everything there is parsed as a catalogue entry and an account is not a tool. Minor and
  not patch: a catalogue can now declare something it could not, and no existing
  catalogue behaves differently until it writes the file. `tests/test_redact.py`,
  `tests/test_redact_backfill.py` and `tests/test_sync_feedback.py` cover it, and
  `docs/new-environment.md` documents it.

- **A profile's `[paths]` may start with `~`.** (consumer-affecting) A profile is tracked,
  and a path spelled out in it carries the account name into the catalogue. Every reader
  of a profile now expands a leading `~` or `~/` to the home directory, forward-slashed as
  profiles already spell their paths, so `~/.claude` and the spelled-out path load equal:
  `radar_lib.load_environments` (every command that takes `--env`), the overlay layer in
  `apply.load_profile` and `bootstrap.load_profile_raw`, which would otherwise report a
  declared `~/.claude` as drift from the path it names. `radar_lib.expand_home` is a pure
  function handed the home; the home is asked of `bootstrap.home_dir()`. `~other/x` and a
  `~` anywhere but the start are left alone. Consumer-affecting because `radar_lib` is a
  contract surface and a profile value that started with `~` used to reach consumers
  verbatim; minor rather than patch, because a profile can now say something it could not.
  `tests/test_environment_home.py` covers the three readers.

- The documents this repository ships: `README.md` (what it is, who it is for, install, and
  the quickstart, measured by running it rather than composed), `docs/concepts.md` (rings,
  axes, the `artifact` dimension, the backing gate and the engine/catalogue split, written
  for a reader with no catalogue of their own and worked through `ruff` and `just`),
  `docs/new-environment.md` (bringing a catalogue up on a machine that has never run it,
  with the agent hand-off kept as an appendix), `CONTRIBUTING.md` and `LICENSE` (MIT). The
  README opens by saying this governs a CLAUDE CODE stack rather than any tool stack: three
  verbs — `reconcile`, `versions`, `field` — read `~/.claude`, so a README that opened on a
  general-purpose radar would be lying on its first page.
- `tests/test_docs.py`, which holds the documents to the two failure modes that are not
  prose failures and are silent either way: a documented verb the CLI does not have (or an
  existing verb the README never names), and a real account name inside a machine path. The
  second borrows `gate._user_literal_hits` rather than restating its regex, because two
  copies of that judgement would drift and the lenient copy is the one nobody notices. What
  it deliberately cannot check is whether a document leaks the SHAPE of a particular
  catalogue: checking names means holding the names, and a public repository holding that
  list would publish the thing the check exists to withhold. That half runs from the
  catalogue, from outside this tree.

- `radar init`, which creates a catalogue instead of documenting how to hand-build one: the
  `radar.toml` marker, an `environments/` profile carrying the paths DISCOVERED on the
  machine rather than placeholders to fill in, an empty `tools/`, a `pyproject.toml` and a
  `CHANGELOG.md` that give the new root its two agreeing version sites, the commit lane
  (`.githooks/`, `.pre-commit-config.yaml`) and a CI workflow. It is the one verb that
  resolves no data root — it is handed the directory that is about to become one, and a
  resolver would search upwards and write into whatever catalogue sat above the target.
  (consumer-affecting)
- `radar add`, which writes one entry with its first dated `[[history]]` block and validates
  it BEFORE the file lands. It refuses what the entry schema rejects and also what the gate
  would fail on the very next command — a `pilot` with no pre-registered exit criteria, an
  `own`/`adopt` entry whose artifact a session can see and which declares no telemetry
  matcher — using the gate's own predicates rather than a second opinion about the same
  rules. The written file is read back and compared to the validated entry, because the
  engine ships with no dependencies and therefore writes TOML with a narrow writer of its
  own. (consumer-affecting)
- `stack_radar/templates/`, the tree `init` writes, as templates with their prose intact: a
  generated file whose comments explain nothing is a file the first reader deletes. Paths
  are never among them — those are computed, because a templated path is a guess and a
  guess in an environment profile fails later and somewhere other than where it was made.
- `bootstrap.home_dir()` and `bootstrap.discovered_paths()`, extracted from that module's
  `main()` so `init` shares one answer to "where does this machine keep its things" rather
  than growing a second. Discovery also gained `claude_config`, the one profile path that is
  a sibling of `claude_home` rather than a file inside it, so a freshly created profile is
  complete for the command that reads it.

- `.github/workflows/ci.yml`, this repository's own continuous integration, on `ubuntu-latest`
  and `windows-latest`: `uv lock --check`, `uv sync`, `ruff format --check`, `ruff check`,
  `ty check src`, `pytest -q`. Two more jobs cover what the suite runs against the checkout's
  own `src/` and could not otherwise catch: `stranger-path` builds the wheel, installs it into
  a venv that has never seen this repository's layout, and runs `radar init` / `add` / `gate`
  / `render` from there in a directory that has never held a catalogue — the packaging
  equivalent of `tests/test_init.py::TestTheStrangerPath`, which reaches the same four verbs
  through `python -m stack_radar.cli` on a `PYTHONPATH` instead. `no-root` runs `radar gate`
  outside any data root and asserts the distinct exit code and the message naming `radar.toml`
  (`stack_radar.paths.DataRootNotFound`). A `zizmor` job audits the workflows
  themselves. `.github/dependabot.yml` watches the two ecosystems this repository has:
  `github-actions` and `uv`.

### Changed

- **The framework check reads the engine's own version.** (consumer-affecting)
  `framework_version()` read the data root's `pyproject.toml`, a placeholder from the time
  the engine lived inside the catalogue it read, so a catalogue's `requires_framework` was
  compared with the catalogue itself and could not fail for a real reason. It now returns
  the engine's `__version__`. A catalogue whose range was written against its own version
  has to name the engine's range instead; a catalogue made by `radar init` already does,
  because `init` derives the range from the engine that runs it. The catalogue's own
  version in its `pyproject.toml` stays its own and no longer enters the check.
  `tests/test_data_root.py::TestRequiresFramework::test_the_range_constrains_the_engine_not_the_catalogue`
  holds it.
- **Install from this repository, at a release tag.** The README and
  `docs/new-environment.md` said `uv tool install stack-radar`, which needs a package on PyPI
  that has not been published. They now install from GitHub at the tag:
  `uv tool install git+https://github.com/grimaldost/radar-engine@v0.2.0`.
- **A field report's `generated_by` is `radar field`.** (consumer-affecting) The report
  `field_report` writes is a contract surface, and it named its generator
  `scripts/field_report.py`: the path of the script the engine ran as before it was
  packaged, which an installed engine does not have. The value is now `radar field`, the
  command that writes the report, and the note under the Markdown title reads
  ``generated by `radar field` - do not hand-edit``. The key, its type and every other
  field are unchanged. A reader tells the two apart by the value: `scripts/field_report.py`
  marks a report written before this change and `radar field` one written after. A
  catalogue's `field/` holds both until its older windows are re-run, because nothing
  rewrites a committed report, so a reader that matched the old string exactly has to
  accept both. No version field is added: the value is enough to tell the two apart, and
  `generated_at` already dates each report. Changed rather than kept, because a
  provenance field that names a file nobody has is wrong in every report written from
  here on. Patch rather than minor: no field is added, removed or renamed.
  `tests/test_field_report.py::TestTheGenerator` asserts the JSON value and the Markdown
  note.

- `pyproject.toml` declares `readme` and `license-files`, which it could not do before the
  files existed; the comment recording that absence goes with them. A published package
  whose `readme` is absent renders an empty page on the index, and nothing else in this
  repository would have noticed. (consumer-affecting)

- **What the commands print no longer points at documents this package does not ship.**
  (consumer-affecting) The gate's messages and the verbs' help text carried the number of
  a decision record, the path of one, or the number of a section of a planning document,
  and `radar bootstrap` named a prompt file that only the catalogue the engine grew up in
  had. None of those is in this repository, so a reader was pointed at a document that
  exists nowhere in the package. Each message keeps its meaning and drops only the
  citation, or states the rule the citation stood for. The invariant lines that carried
  the tag now read `[FAIL] invariant:` and `[NOTE] invariant: holds`, and the skipped-check
  line reads `skipped: the invariant scan`. The engine-name, publication-check and
  `<claude_home>/feedback` notes lose the parenthesis and keep the reason they already
  stated. A machine-local term's publication finding says why the name must not travel
  instead of naming the record. The `[version].policy=latest` error states the identity
  rule itself: a registry is taken from the tool's repository metadata, never found by
  matching a name. The note for an engine with no `own` entry yet says that writing the
  entry gives it a name. `radar bootstrap` ends by sending its reader to
  `docs/new-environment.md` in the engine's documentation for the policy decisions.
  `radar gate --help` names the invisible-hand invariant, every `--data-root` help says
  "for CI and nothing else" without the number, and the artifact-view paragraph that
  `radar render` writes ends at the sentence. Consumer-affecting because a script that
  matches the old text of a gate or validation line stops matching; no exit code and no
  finding moves. Patch and not minor: nothing gains or loses a capability, and only the
  wording of what was already printed changes.

### Fixed

- **`radar render --help` prints help instead of rendering.** `render` had no parser and
  read `--public` straight from argv, so `--help` fell through to a full render that
  rewrote the catalogue's tracked `README.md` and `docs/radar/index.html`. It now parses its
  options first, lists `--public` and `--data-root`, and refuses an option it does not
  know. `tests/test_init.py::TestTheStrangerPath::test_render_help_prints_the_options_and_writes_nothing`
  holds it.
- **The hints the commands print name `radar` verbs, not script files.**
  (consumer-affecting) The engine was extracted from a catalogue's `scripts/` directory,
  and the next steps it printed still told the reader to run files an installed engine
  does not have. `radar bootstrap` now ends with `radar apply --env <env> --plan` and
  `radar gate` instead of `python scripts/apply.py ...` and `python scripts/gate.py`. The
  gate's two scope-leak findings say to run `radar redact-backfill` instead of
  `python scripts/redact_backfill.py`. `radar reconcile` says to re-run `radar gate`
  instead of `gate.py`, `radar render-feedback-targets` without `--env` prints
  `usage: radar render-feedback-targets ...` instead of naming its module file, and the
  note `radar redact-backfill` prints after renaming feedback files names
  `radar sync-feedback` as the step that regenerates the indexes. Each command was checked
  against the verb's own `--help`; `--plan` is `radar apply`'s alias for `--check`.
  Consumer-affecting because `gate.py` is a contract surface and a script that matches the
  old text of a scope-leak line stops matching; no exit code and no finding moves. Patch
  and not minor: every hint named a command that could not be run, and nothing gains a
  capability.

- **The README `radar render` writes names the `radar` verbs.** (consumer-affecting) Its
  header said the file was generated by `scripts/render.py`, and its workflow line sent
  the reader to `scripts/snapshot.py`, `scripts/gate.py` and `scripts/render.py`. The
  header now names `radar render`, and the workflow line `radar snapshot`, `radar gate`
  and `radar render`. The overlay
  `radar bootstrap --write` puts in `environments/<env>.local.toml` says it was written by
  `radar bootstrap` instead of `scripts/bootstrap.py`. Consumer-affecting because
  `README.md` is a file every catalogue commits: its next render changes those two lines,
  and a catalogue that freezes its rendered README in a byte-parity fixture will see them
  differ. No fixture in this repository pins the file, and none could byte for byte,
  because the header carries the render date. The overlay is gitignored and changes only
  when `bootstrap --write` runs again. `tests/test_init.py::TestTheStrangerPath` now
  asserts the header and the workflow line on a freshly rendered catalogue. Patch and not
  minor: the wording of two generated lines changes, and nothing gains a capability.

- **The starter profile writes the home as `~`.** `radar init` and `bootstrap --write`
  wrote the discovered absolute paths into the tracked `environments/<env>.toml`, so a
  new catalogue's first commit named the account, and `redact-backfill --check` refused
  it. `bootstrap.collapse_home` writes a leading home back as `~`, which every reader
  already expands (`radar_lib.with_home`); a sibling directory the home is only a prefix
  of is left as written.
- **`field_report` maps the declared extra homes too.** It was the one writer that asked
  only for this machine's home, so a session synced from another workstation kept that
  machine's slug in the written report. It now puts every home from
  `redact.local.toml` through `redact_homes`, the same as `redact-backfill` and
  `sync-feedback`.
- **`redact-backfill` maps the home directory out of every tracked text file, and
  `--check` answers with its exit code.** The command only knew scoped names, and it
  stopped early when no machine-local entry declared one. `field_report` maps the home out
  of what it writes, which does nothing for files a catalogue committed before it did - and
  those are not only field reports: an archived feedback report quotes the path it ran in,
  a spec records where a session worked, a profile declares its paths. The home pass
  (`redact.redact_home`, every spelling: native, forward-slash, JSON-escaped and slug)
  now walks every tracked text file, the scoped-name pass's reach, and runs before the
  scoped names, the order the writer uses. Binary suffixes are skipped as before. A
  profile keeps working after the pass because its readers expand `~` (above). `--check`
  exited 0 whatever it found, so "would rewrite 3 file(s)" and "would rewrite 0" were the
  same answer to a shell; it now exits 1 when any pass would change a file, which is what
  lets a commit hook run it. `tests/test_redact_backfill.py` is new.

- **`sync-feedback` maps the home directory out of a report on the way in.** It already
  mapped scoped names out of the archived copy; a session also writes the paths it worked
  in, and the archive is tracked, so every sync could put the account back into a
  catalogue the backfill had just cleaned. The archived body is now the inbox body with
  the home mapped first and the scoped names second; the inbox keeps the real paths. An
  archive that still holds a report ingested with the home in it reports that report as a
  conflict until `redact-backfill` has run over it - the comparison is against the
  redacted body, as it already was for scoped names. `tests/test_sync_feedback.py::
  TestIngestMapsTheHomeOut` covers it.

- **The astroturf check trusted a downloads figure the entry no longer declared a
  `registry` for.** `gate.py`'s astroturf-signature condition compared `downloads_month`
  against `stars` without checking `registry`, so a figure `snapshot.py` had carried
  forward from a REMOVED or changed registry kept being flagged as this entry's own.
  Observed on a catalogue entry whose wrong `registry` was corrected away after a
  download figure had already been measured under it: the snapshot's carry-forward kept
  the figure the wrong registry had produced. The signature check now requires
  `registry`, matching the sibling "no download figure" check three lines above it; an
  entry with a carried figure and no `registry` now prints a NOTE that the check could
  not run, rather than flagging it or staying silent. `snapshot.py` now compares a
  carried figure's recorded
  `downloads_source` against the entry's current `registry` and drops the figure instead
  of carrying it forward on a mismatch, so a corrected or removed registry stops
  producing the false figure at the source; an entry that has never declared a registry
  (no `downloads_source` recorded) is unaffected. `tests/test_gate_astroturf.py` and
  `tests/test_snapshot_registry_drift.py` cover both.

- **`versions.py`'s probe could resolve a global tool from the running project's own
  virtualenv.** Run under `uv run`, this process's own PATH has the project's
  `.venv/Scripts` (or `.venv/bin`) ahead of everything else, and `VIRTUAL_ENV` names it -
  the arrangement that lets the project find its own pinned tools. `run()`'s bare
  `shutil.which(argv[0])` read that same PATH, so a probe meant to ask "what does the
  operator have installed globally" could resolve the project's pinned copy instead - a
  dev dependency of this very repository, in `ruff`'s case - and compare IT against
  upstream, reporting a current global install as behind. `run()` now searches
  `global_probe_env()`: this process's environment with `VIRTUAL_ENV` and its own
  `Scripts`/`bin` directory stripped from `PATH`, matched by directory identity rather
  than a string prefix. `tests/test_versions_probe.py` reproduces the shadowing with a
  fake project venv directory ahead of a fake global one.

- **A written field report no longer names the machine's account.** (consumer-affecting)
  `field_report` wrote the home directory into every report without anyone writing it
  down: in `claude_home`, in the archive store under `--reconcile`, and in every project
  slug, because Claude Code files a session under its working directory with each
  character outside ASCII letters and digits turned into `-`, so a session run under the
  home is filed under a name that starts with the home in that spelling. `field/` is
  tracked, so the account travelled with the catalogue. The written `.md` and `.json` now
  carry `~` for the home in each spelling it takes - native, forward-slash, JSON-escaped
  and slug - so `claude_home` reads `~/.claude` (in the platform's separator) and a slug
  reads `~-Documents-<project>`. `--json` to stdout keeps the real paths, the same split
  the scoped-name redaction already made, because whoever runs it is on the machine that
  owns them. The home comes from `bootstrap.home_dir()` at run time; the mapping is
  `redact.redact_home()`, a pure function handed the home, which declines a bare root
  rather than turning every path into `~`. A path spelling only counts when it is not the
  head of a longer name (an `-old` sibling, a `.bak` copy); the drive letter of a slug is
  matched in either case, because both occur in one real corpus.

  Consumer-affecting because a reader that took `claude_home`, `reconcile.store` or a
  `slug` from a written report as a real path now gets a placeholder. Patch rather than
  minor: no field is added, removed or renamed. `tests/test_redact.py` covers each spelling
  on both path flavours, the near misses left alone, idempotence and the bare root;
  `tests/test_field_report.py::TestHomeRedaction` runs the report with a chosen home and
  holds the written JSON equal, byte for byte, to the unredacted `--json` document of the
  same run with only the home mapped. The fixture gains `Radar.user_home` for that.

- **`snapshot.py` had no argument parser, so `--help` started a real refresh.** `main()`
  called `begin_command()` unconditionally as its first line; there was nothing to
  recognise `-h`/`--help` before that, so it reached the network-touching loop
  (`gh api` per tool, plus a snapshot write) exactly like a bare invocation would. It now
  builds an `argparse` parser that declares `--data-root` and exits 2 on an unrecognised
  argument, before `begin_command()` runs; `--data-root` still resolves through the same
  override argument every other command already passes it, so its behaviour is
  unchanged. `tests/test_snapshot.py` proves it: `begin_command` is monkeypatched to
  raise, so a run that reaches it fails the test rather than the network.

- **The names of the operator's private tools leave the source, and the exemption list
  leaves the code.** The requirement was that the private repository's
  publication check, pointed here, is GREEN over this whole tree. It was declared met
  without being run: measured, it returned **39 findings**, and **16 of them were the
  names of six private catalogue entries** in ELEVEN tracked files — six under `src/`, five
  under `tests/`. The debt was even recorded in an earlier commit body as still to be
  resolved, and the change shipped without touching it.

  What was rewritten, and the rule applied: the REASONING in a comment is what makes it
  worth keeping, and the IDENTITY of a sibling repository is what must not travel. So the
  changelog gate's provenance docstring still says it was ported rather than invented,
  still names the tie-break rule, still explains why the opt-out is the half that makes
  the rule livable, and still records that this is the second port and that five rounds of
  review upstream found four ways the first was wrong — it just no longer says whose. The
  same for the command scanner's provenance, the version checker's registry-collision
  example (a short, ordinary tool name has returned an unrelated package at a LOWER
  version), and the worktree examples in the gate, which now read `{documents}/<tool>`.

  `INVARIANT_EXEMPT` was the sharpest case, because it was not prose: a module constant
  whose KEY spelled a path inside a repository that is not this one. It moves to
  `radar.toml [invariant].exempt`, read by `declared_exempt()` and passed into
  `check_invariant` the way the needles already were — the same move the needles made, one
  step later, and for the same reason: a list that is true of one estate is invisible on
  the day it stops being. FIVE tests cover it, and three of them exist because a one-line
  mutation survived the first two: replacing the suffix lookup with "exempt any file as
  soon as one exemption is declared" left both green AND the whole suite green, while
  switching the invariant scan off and still printing the sentence a real pass produces.
  The five: a declared exemption spares the file and prints its reason; the identical file
  with the key absent bites; a second file the key does not name still bites in the SAME
  run; a key that is a substring but not a suffix does not spare; and a malformed
  declaration is ignored with a note rather than silently.

  `eval_status`'s closed vocabulary loses the term built from a private tool's name; the
  value is `eval-pending`. That term was part of the DATA CONTRACT rather than a comment,
  so a published validator carried the name inside the schema it enforces. It is not
  written out here for the reason this whole entry exists: a changelog is a tracked file
  in the tree the check reads, so documenting a removal in the removed words puts them
  straight back. That is exactly what the first draft of this entry did, and an
  independent verification caught it.

  Measured after, and the numbers are the corrected ones: **22 findings, none of them a
  private tool's name**. 16 are this engine's own name, a forbidden term only until the
  private repository renames itself. 6 are that repository's two FORM rules, and they split
  three ways rather than the two the first draft claimed: 2 fire on tests that deliberately
  ASSERT the rule (a negative assertion is the opposite of a violation); 3 fire on prose in
  production modules — comment, docstring, and the gate's own error message, so the check
  reports the text of the rule as a breach of it; and 1 is incidental, a fixture belonging to
  the visibility rule that trips the literal rule by accident rather than by assertion. Neither half is fixed here: the check is not edited in the change it judges,
  and a scrub that rewrites a rule's own wording to silence it is the failure this entry is
  about.

  A NOTE ON HOW THE FIRST DRAFT GOT THOSE NUMBERS WRONG, because the mechanism generalises:
  the classifier used to sort the findings matched the needle against the whole finding
  line, and a finding line carries the FILE PATH. This package is `src/stack_radar/`, so
  every finding in it matched the needle regardless of its actual cause, and three form
  violations in production modules were filed as "the engine's own name". The fix is to
  classify on the REASON the check prints, which is structured, and to report anything
  unrecognised as unclassified rather than into a default bucket.

## [0.1.0] - 2026-09-13

The first release. A tech radar for a Claude Code stack, and the gate behind it.

### Added

- `radar`, a command-line entry point with one subcommand per verb: `gate`, `render`,
  `apply`, `field`, `reconcile`, `snapshot`, `versions`, `sync-feedback`,
  `render-feedback-targets`, `redact-backfill`, `bootstrap`, `changelog-gate` and
  `check-version-sites`. Each verb keeps its own parser, its own `--help` and its own
  exit codes; the launcher resolves the verb and stays out of the way.
- `stack_radar.paths`, the data-root resolver. A catalogue is a directory carrying a
  `radar.toml` at its top, and the engine finds one by walking up from the working
  directory the way `git` finds `.git`. `--data-root` and `RADAR_DATA_ROOT` name one
  explicitly, for CI; each still has to point at a directory that carries the marker.
- `stack_radar.gate`, the backing gate: entry schema, licence and staleness checks,
  telemetry keys, pilot-exit criteria, version sites, and a scan asserting that the stack
  under governance never references the catalogue that governs it.
- `stack_radar.field_report`, session-transcript telemetry per entry;
  `stack_radar.render`, the catalogue's artefacts; `stack_radar.apply` and
  `stack_radar.reconcile`, convergence against an environment profile;
  `stack_radar.versions` and `stack_radar.snapshot`, upstream release and licence state.
- `stack_radar.changelog_gate`, which fails a change that moves this repository's code
  without recording it. It reads `[changelog]` from a `radar.toml` when it is run inside a
  catalogue, and uses this repository's own defaults otherwise — one engine serves
  repositories whose answers differ, and a gate watching a prefix that does not exist
  passes in silence. (consumer-affecting)
- Three version sites instead of two — `pyproject.toml`, this file, and
  `stack_radar.__version__` — compared by `radar check-version-sites` and by the gate.
  The third arrived with distribution: a package that ships carries its version in its
  own namespace, and the site is optional so that a catalogue with two keeps working
  against the same verb. (consumer-affecting)
