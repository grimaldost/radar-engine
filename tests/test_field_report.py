"""Behavioural tests for the field-telemetry miner.

Each test is a defect that was measured on the real corpus on 2026-09-03, reduced to
the smallest synthetic transcript that reproduces it. They run the script as a
subprocess against a miniature radar built in tmp_path (see conftest.py), so they
exercise the whole path - argument parsing, scan, census, findings - rather than an
internal function that a refactor could route around.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

from conftest import bash, note, tool_result, tool_use


def epoch(day: str) -> float:
    """Midday UTC on `day`, as an mtime."""
    d = dt.date.fromisoformat(day)
    return dt.datetime(d.year, d.month, d.day, 12, tzinfo=dt.UTC).timestamp()


WINDOW = ("--since", "2026-08-01", "--until", "2026-08-31")


def entry(name: str, *, ring: str = "adopt", body: str = "") -> str:
    return f'name = "{name}"\nring = "{ring}"\nartifact = "cli"\n{body}'


def cli_entry(name: str, globs: str, *, ring: str = "adopt", extra: str = "") -> str:
    return entry(
        name,
        ring=ring,
        body=f'\n[telemetry]\nmatch_command = [{globs}]\nsince = "2026-08-01"\n{extra}',
    )


class TestCommandMatching:
    def test_heredoc_body_is_not_an_invocation(self, radar):
        radar.tool("ruff", cli_entry("ruff", '"ruff"'))
        radar.session(
            "s1",
            [
                bash("t1", "2026-08-05T10:00:00.000Z", "git commit -F - <<'MSG'\nran ruff\nMSG"),
                bash("t2", "2026-08-05T10:01:00.000Z", "cat <<EOF > note.txt\nruff check\nEOF"),
            ],
        )
        doc = radar.report("ruff", *WINDOW)
        assert doc["metrics"]["invocations"] == 0

    def test_quoted_python_body_is_not_an_invocation(self, radar):
        # A variable named after a tool at the head of a line inside `python -c` is
        # a command line to a newline-splitting parser. `ty` read 257 against 221
        # real invocations, and bodies like this are where the difference came from.
        radar.tool("ty", cli_entry("ty", '"ty"'))
        radar.session(
            "s1",
            [
                bash(
                    "t1",
                    "2026-08-05T10:00:00.000Z",
                    "python -c \"\nimport json\nty = json.load(open('x'))\nprint(ty)\n\"",
                ),
            ],
        )
        doc = radar.report("ty", *WINDOW)
        assert doc["metrics"]["invocations"] == 0

    def test_multiline_quoted_argument_is_not_an_invocation(self, radar):
        # A commit message body is the commonest multi-line quoted argument there is.
        radar.tool("ruff", cli_entry("ruff", '"ruff"'))
        radar.session(
            "s1",
            [
                bash(
                    "t1",
                    "2026-08-05T10:00:00.000Z",
                    'git commit -m "style: reformat\n\nruff format across the tree"',
                ),
            ],
        )
        doc = radar.report("ruff", *WINDOW)
        assert doc["metrics"]["invocations"] == 0

    def test_runner_prefix_still_resolves_the_delegated_tool(self, radar):
        # A prefix in front of the runner (`time`, `wsl`, `xargs -n1`) hid the whole
        # chain from the old head-of-segment reading, which undercounted instead.
        radar.tool("ruff", cli_entry("ruff", '"ruff"'))
        radar.session(
            "s1",
            [
                bash("t1", "2026-08-05T10:00:00.000Z", "time uv run ruff check ."),
                tool_result("t1", "2026-08-05T10:00:01.000Z"),
            ],
        )
        doc = radar.report("ruff", *WINDOW)
        assert doc["metrics"]["invocations"] == 1
        assert doc["metrics"]["per_tool"][0]["tool"] == "cmd:ruff"

    def test_module_invocation_counts_the_module(self, radar):
        radar.tool("pytest", cli_entry("pytest", '"pytest"'))
        radar.session(
            "s1",
            [bash("t1", "2026-08-05T10:00:00.000Z", "uv run python -m pytest tests/ -q")],
        )
        doc = radar.report("pytest", *WINDOW)
        assert doc["metrics"]["invocations"] == 1

    def test_environment_assignment_is_not_the_executable(self, radar):
        # The assignment's value is a path fragment, not a command. Quote a Windows
        # path with a space in it and a whitespace-splitting parser reads the tail of
        # the path as the executable - here `Files/ruff"` becomes a ruff invocation.
        radar.tool("ruff", cli_entry("ruff", '"ruff"'))
        radar.session(
            "s1",
            [
                bash(
                    "t1",
                    "2026-08-05T10:00:00.000Z",
                    'F="C:/Program Files/ruff" git -C "$F" status',
                )
            ],
        )
        doc = radar.report("ruff", *WINDOW)
        assert doc["metrics"]["invocations"] == 0

    def test_a_module_invocation_matches_the_distribution_name(self, radar):
        # THE MEASURED MISS. cmdscan resolves `python -m X` to the MODULE name, and
        # a hyphenated distribution is imported with an underscore. This machine runs
        # pre-commit only that way - Smart App Control blocks the unsigned .exe shim -
        # so the entry's glob could not match the estate's own invocation form and the
        # report read 32 invocations over 2026-07-26..2026-09-03 where 71 were made.
        # Note the transcript never contains the hyphenated spelling, so this also
        # pins the byte prescan: with only `pre-commit` as a needle the file is never
        # opened and the miss is invisible rather than merely wrong.
        radar.tool("pre-commit", cli_entry("pre-commit", '"pre-commit"'))
        radar.session(
            "s1",
            [bash("t1", "2026-08-05T10:00:00.000Z", "uv run python -m pre_commit run -a")],
            mtime=epoch("2026-08-05"),
        )
        doc = radar.report("pre-commit", *WINDOW)
        assert doc["metrics"]["invocations"] == 1
        assert doc["metrics"]["per_tool"][0]["tool"] == "cmd:pre_commit"

    def test_the_report_states_the_globs_it_actually_matched_on(self, radar):
        # An expansion the reader cannot see is an undeclared matcher. The document
        # carries the effective set, which is also what --reconcile counts the
        # archive's table under.
        radar.tool("pre-commit", cli_entry("pre-commit", '"pre-commit"'))
        radar.session("s1", [note("2026-08-05T10:00:00.000Z")], mtime=epoch("2026-08-05"))
        doc = radar.report("pre-commit", *WINDOW)
        assert doc["match_command"] == ["pre-commit", "pre_commit"]

    def test_a_pattern_glob_is_left_alone(self, radar):
        # Only a LITERAL glob gets its module spelling. In a pattern the hyphen may be
        # structural - inside a character class it is a range, and `[a-c]` respelled is
        # the set {a, _, c} - so the rewrite would change what the author wrote. An
        # entry that needs both spellings under a pattern declares both.
        radar.tool("pre-commit", cli_entry("pre-commit", '"pre-commit*"'))
        radar.session(
            "s1",
            [bash("t1", "2026-08-05T10:00:00.000Z", "uv run python -m pre_commit run -a")],
            mtime=epoch("2026-08-05"),
        )
        doc = radar.report("pre-commit", *WINDOW)
        assert doc["metrics"]["invocations"] == 0
        assert doc["match_command"] == ["pre-commit*"]

    def test_a_command_override_is_measured_literally(self, radar):
        # The expansion exists for DECLARED globs: an entry is written under the name the
        # tool installs and publishes under, and Python packaging turns that hyphen into
        # an underscore on import. An override is a probe - somebody typed a glob to find
        # out what it matches - so silently measuring a second glob they did not type
        # answers a different question from the one asked, which is what a probe is for.
        radar.tool("pre-commit", cli_entry("pre-commit", '"pre-commit"'))
        radar.session(
            "s1",
            [bash("t1", "2026-08-05T10:00:00.000Z", "uv run python -m pre_commit run -a")],
            mtime=epoch("2026-08-05"),
        )
        doc = radar.report("pre-commit", "--match-command", "pre-commit", *WINDOW)
        assert doc["match_command"] == ["pre-commit"]
        assert doc["metrics"]["invocations"] == 0

    def test_the_declared_glob_is_still_expanded(self, radar):
        # The boundary the change must not cross: same entry, same transcript, no override.
        radar.tool("pre-commit", cli_entry("pre-commit", '"pre-commit"'))
        radar.session(
            "s1",
            [bash("t1", "2026-08-05T10:00:00.000Z", "uv run python -m pre_commit run -a")],
            mtime=epoch("2026-08-05"),
        )
        doc = radar.report("pre-commit", *WINDOW)
        assert doc["match_command"] == ["pre-commit", "pre_commit"]
        assert doc["metrics"]["invocations"] == 1

    def test_the_help_describes_the_resolved_chain(self, radar):
        # The flag's help said "the EXECUTABLE at the head of a Bash/PowerShell command",
        # which is what the PREDECESSOR matched. The current matcher resolves the whole
        # chain - the entire point of the cmdscan port - so the help documented a
        # behaviour the code had already stopped having.
        out = radar.run("--help").stdout
        assert "RESOLVED CHAIN" in out
        assert "EXECUTABLE at the head" not in out
        assert "measured LITERALLY" in out

    def test_windows_executable_suffix_is_normalised(self, radar):
        # `.venv/Scripts/ruff.exe` is a ruff invocation; a parser that keeps the
        # suffix compares `ruff.exe` against the glob `ruff` and silently drops it.
        radar.tool("ruff", cli_entry("ruff", '"ruff"'))
        radar.session(
            "s1",
            [bash("t1", "2026-08-05T10:00:00.000Z", "./.venv/Scripts/ruff.exe check .")],
        )
        doc = radar.report("ruff", *WINDOW)
        assert doc["metrics"]["invocations"] == 1


class TestCensus:
    def test_session_is_placed_by_its_own_records_not_its_mtime(self, radar):
        # The measured defect: on 2026-08-26 an unidentified process rewrote 34 main
        # transcripts whole, and 27 of them held June/July sessions. Under the mtime
        # rule every one of them entered the 2026-07-26.. denominator.
        radar.tool("ruff", cli_entry("ruff", '"ruff"'))
        radar.session(
            "inside",
            [
                note("2026-08-10T09:00:00.000Z"),
                bash("t1", "2026-08-10T10:00:00.000Z", "uv run ruff check ."),
                note("2026-08-10T11:00:00.000Z"),
            ],
            mtime=epoch("2026-08-10"),
        )
        radar.session(
            "rewritten-june",
            [note("2026-06-02T09:00:00.000Z"), note("2026-06-02T10:00:00.000Z")],
            mtime=epoch("2026-08-26"),  # touched inside the window, written long before
        )
        doc = radar.report("ruff", *WINDOW)
        m = doc["metrics"]
        assert m["sessions_in_window"] == 1
        assert m["transcripts_excluded_by_timestamp"] == 1

    def test_the_exclusion_count_is_named_for_the_files_it_counts(self, radar):
        # It counts FILES - the loop runs once per transcript - and was reported as
        # `sessions_excluded_by_timestamp`. A session with a second, in-window transcript
        # is not excluded from the census by one of its files being, so a file count named
        # after sessions invites subtracting it from the denominator.
        radar.tool("ruff", cli_entry("ruff", '"ruff"'))
        radar.session(
            "rewritten-june",
            [note("2026-06-02T09:00:00.000Z"), note("2026-06-02T10:00:00.000Z")],
            mtime=epoch("2026-08-26"),
        )
        m = radar.report("ruff", *WINDOW)["metrics"]
        assert "sessions_excluded_by_timestamp" not in m
        assert m["transcripts_excluded_by_timestamp"] == 1

    def test_the_markdown_surfaces_the_transcripts_still_dated_by_mtime(self, radar):
        # The census falls back to the superseded mtime rule for a transcript with no
        # parseable timestamp at either end. The number existed in the JSON and appeared
        # nowhere a reader looks, so a census resting partly on the old rule read as one
        # resting entirely on the new one.
        radar.tool("ruff", cli_entry("ruff", '"ruff"'))
        (radar.home / "projects" / "C--Users-x-Documents-p").mkdir(parents=True, exist_ok=True)
        undated = radar.home / "projects" / "C--Users-x-Documents-p" / "no-clock.jsonl"
        undated.write_text('{"type":"user","message":{"role":"user","content":[]}}\n')
        import os

        os.utime(undated, (epoch("2026-08-10"), epoch("2026-08-10")))
        radar.run("ruff", *WINDOW)
        md = (radar.root / "field" / "ruff" / "2026-08-01_2026-08-31.md").read_text(
            encoding="utf-8"
        )
        assert "main transcripts still dated by mtime" in md
        assert "| 1 |" in md
        assert "transcripts excluded by the timestamp rule" in md

    def test_a_session_straddling_the_start_still_counts(self, radar):
        # Overlap, not containment: a session that began before `since` and ran into
        # the window had opportunity inside it. The mtime here is AFTER the window,
        # which is the same rewrite hazard pointing the other way - under the mtime
        # rule this session dropped out of the denominator instead of into it.
        radar.tool("ruff", cli_entry("ruff", '"ruff"'))
        radar.session(
            "straddler",
            [note("2026-07-30T09:00:00.000Z"), note("2026-08-02T10:00:00.000Z")],
            mtime=epoch("2026-09-02"),
        )
        doc = radar.report("ruff", *WINDOW)
        assert doc["metrics"]["sessions_in_window"] == 1
        assert doc["metrics"]["transcripts_excluded_by_timestamp"] == 0


class TestDenominator:
    """One census for every row, or a --all table's column is several censuses.

    The denominator used to be seeded from the index and then grown with the sessions an
    entry's own invocations touched. Those extra ids come from sidechain transcripts
    (subagents, workflows) whose parent session's own transcript is absent or out of
    window - real use, but not a session of its own. So an entry that runs work in
    subagents divided by a bigger number than the entry beside it in the same table.
    """

    def _board(self, radar):
        radar.tool("ruff", cli_entry("ruff", '"ruff"'))
        radar.tool("widget", cli_entry("widget", '"widget"'))
        # Two ordinary sessions, in the census.
        for i in range(2):
            radar.session(
                f"plain{i}",
                [note("2026-08-06T09:00:00.000Z"), note("2026-08-06T10:00:00.000Z")],
                mtime=epoch("2026-08-06"),
            )
        # A sidechain whose parent has no main transcript at all: `widget` is reached only
        # from inside it, which is exactly the shape that used to inflate one row.
        radar.session(
            "orphan-parent",
            [bash("t9", "2026-08-05T10:00:00.000Z", "uvx widget check")],
            sub="subagents/a.jsonl",
            mtime=epoch("2026-08-05"),
        )

    def test_a_sidechain_only_session_does_not_enter_the_denominator(self, radar):
        self._board(radar)
        m = radar.report("widget", *WINDOW)["metrics"]
        assert m["sessions_in_window"] == 2
        assert m["sessions_with_use"] == 1
        assert m["sessions_with_use_outside_census"] == 1

    def test_every_row_of_all_divides_by_the_same_census(self, radar):
        self._board(radar)
        p = radar.run("--all", *WINDOW)
        assert p.returncode == 0, p.stdout + p.stderr
        rows = [ln.split() for ln in p.stdout.splitlines() if ln.startswith(("ruff ", "widget "))]
        denominators = {r[3].split("/")[1] for r in rows}
        assert denominators == {"2"}, p.stdout

    def test_the_finding_says_where_the_extra_sessions_came_from(self, radar):
        self._board(radar)
        doc = radar.report("widget", *WINDOW)
        hit = [f for f in doc["findings"] if "not in the window census" in f]
        assert len(hit) == 1 and hit[0].startswith("[NOTE]")
        assert "sidechain" in hit[0]


class TestRetention:
    def test_replayed_history_pulls_the_horizon_back(self, radar):
        # THE DEFECT: the horizon was the oldest FIRST record, and a resumed session
        # replays its predecessor - the replayed rows keep their original, earlier
        # timestamps while sitting after the resume header. So the earliest record in a
        # file can predate its first one, and the reported horizon was four days late on
        # the live corpus (2026-06-19 against a true 2026-06-15).
        radar.tool("ruff", cli_entry("ruff", '"ruff"'))
        radar.session(
            "resumed",
            [
                note("2026-08-14T09:00:00.000Z", "resumed session header"),
                note("2026-08-02T08:00:00.000Z", "replayed from the earlier session"),
                note("2026-08-14T10:00:00.000Z"),
            ],
            mtime=epoch("2026-08-14"),
        )
        doc = radar.report("ruff", *WINDOW)
        assert doc["scan"]["oldest_record_by_head_scan"] == "2026-08-02"
        # And the census is unmoved: the session's own clock still starts 2026-08-14, so
        # a replayed timestamp cannot drag a session into an older window's denominator.
        assert doc["metrics"]["sessions_in_window"] == 1

    def test_the_warning_says_the_date_is_a_ceiling(self, radar):
        radar.tool("ruff", cli_entry("ruff", '"ruff"'))
        radar.session(
            "s1",
            [note("2026-08-14T09:00:00.000Z"), note("2026-08-14T10:00:00.000Z")],
            mtime=epoch("2026-08-14"),
        )
        doc = radar.report("ruff", *WINDOW)
        warn = next(f for f in doc["findings"] if "truncated by retention" in f)
        assert "BOUNDED HEAD SCAN" in warn
        assert "CEILING" in warn
        assert "64 KiB" in warn

    def test_warns_when_the_oldest_transcript_starts_after_since(self, radar):
        # Transcripts are pruned. A window that reaches back further than the oldest
        # surviving file produces a truncated census that looks like a complete one.
        radar.tool("ruff", cli_entry("ruff", '"ruff"'))
        radar.session(
            "s1",
            [note("2026-08-14T09:00:00.000Z"), note("2026-08-14T10:00:00.000Z")],
            mtime=epoch("2026-08-14"),
        )
        doc = radar.report("ruff", *WINDOW)
        warn = [f for f in doc["findings"] if "truncated by retention" in f]
        assert warn, doc["findings"]
        assert "2026-08-14" in warn[0]
        assert warn[0].startswith("[WARN]")

    def test_no_warning_when_the_corpus_reaches_the_window_start(self, radar):
        radar.tool("ruff", cli_entry("ruff", '"ruff"'))
        radar.session(
            "s1",
            [note("2026-07-20T09:00:00.000Z"), note("2026-08-14T10:00:00.000Z")],
            mtime=epoch("2026-08-14"),
        )
        doc = radar.report("ruff", *WINDOW)
        assert not [f for f in doc["findings"] if "truncated by retention" in f]


class TestReplayedHistory:
    def test_a_resumed_session_does_not_double_count_the_replayed_calls(self, radar):
        # Resuming or forking a session writes a NEW transcript that replays the
        # earlier one verbatim, tool_use ids included - 3920 of the archive's 71349
        # rows, ~5.5%. Counting both copies counts one event twice.
        radar.tool("ruff", cli_entry("ruff", '"ruff"'))
        replayed = [
            note("2026-08-05T09:59:00.000Z"),
            bash("t1", "2026-08-05T10:00:00.000Z", "uv run ruff check ."),
            tool_result("t1", "2026-08-05T10:00:01.000Z"),
        ]
        radar.session("a-original", replayed, mtime=epoch("2026-08-05"))
        radar.session(
            "b-resumed",
            [
                *replayed,
                bash("t2", "2026-08-06T10:00:00.000Z", "uv run ruff format ."),
                tool_result("t2", "2026-08-06T10:00:01.000Z"),
            ],
            mtime=epoch("2026-08-06"),
        )
        m = radar.report("ruff", *WINDOW)["metrics"]
        assert m["invocations"] == 2
        assert m["replayed_dropped"] == 1

    def test_the_oldest_file_keeps_the_call(self, radar):
        # Which copy survives matters for the per-session attribution: the original
        # session is the one that made the call.
        radar.tool("ruff", cli_entry("ruff", '"ruff"'))
        replayed = [bash("t1", "2026-08-05T10:00:00.000Z", "uv run ruff check .")]
        radar.session("a-original", replayed, mtime=epoch("2026-08-05"))
        radar.session("b-resumed", list(replayed), mtime=epoch("2026-08-06"))
        doc = radar.report("ruff", *WINDOW)
        assert doc["metrics"]["invocations"] == 1
        assert [i["session"] for i in doc["invocations"]] == ["a-original"]


PILOT_EXIT = """
[pilot_exit]
review_after_days = 30
adopt_if = "used somewhere"
decline_if = "used nowhere"
min_sessions_with_use = 5
"""


class TestPilotExitGating:
    def _entry(self, ring: str) -> str:
        return (
            cli_entry("serena", '"serena"', ring=ring)
            + PILOT_EXIT
            + '\n[[history]]\ndate = "2026-07-26"\nring = "pilot"\n'
            + ('\n[[history]]\ndate = "2026-08-24"\nring = "observe"\n' if ring != "pilot" else "")
        )

    def test_criteria_are_not_evaluated_once_the_pilot_has_ended(self, radar):
        # serena and zizmor are both at observe and both keep their [pilot_exit] on
        # purpose. Reporting "pilot review is due" for an experiment that ended is a
        # finding no action can follow.
        radar.tool("serena", self._entry("observe"))
        radar.session("s1", [note("2026-08-10T09:00:00.000Z")], mtime=epoch("2026-08-10"))
        doc = radar.report("serena", *WINDOW)
        assert not [f for f in doc["findings"] if "pilot review is due" in f]
        assert not [f for f in doc["findings"] if "min_sessions_with_use" in f]
        assert doc["checks"] == []
        assert doc["pilot_exit_evaluated"] is False
        retained = [f for f in doc["findings"] if "retained from an earlier pilot" in f]
        assert len(retained) == 1
        assert retained[0].startswith("[NOTE]")

    def test_criteria_are_evaluated_while_the_entry_is_in_pilot(self, radar):
        radar.tool("serena", self._entry("pilot"))
        radar.session("s1", [note("2026-08-10T09:00:00.000Z")], mtime=epoch("2026-08-10"))
        doc = radar.report("serena", *WINDOW)
        assert doc["pilot_exit_evaluated"] is True
        assert [c["check"] for c in doc["checks"]] == [
            "review_after_days",
            "min_sessions_with_use",
        ]

    def test_the_clock_runs_from_the_last_pilot_entry(self, radar):
        # An entry that piloted, was parked and was re-piloted is days into the
        # CURRENT experiment, not into the one it started a year ago.
        toml = (
            cli_entry("serena", '"serena"', ring="pilot")
            + PILOT_EXIT
            + '\n[[history]]\ndate = "2026-01-01"\nring = "pilot"\n'
            + '\n[[history]]\ndate = "2026-03-01"\nring = "observe"\n'
            + '\n[[history]]\ndate = "2026-09-01"\nring = "pilot"\n'
        )
        radar.tool("serena", toml)
        radar.session("s1", [note("2026-08-10T09:00:00.000Z")], mtime=epoch("2026-08-10"))
        doc = radar.report("serena", *WINDOW)
        rad = next(c for c in doc["checks"] if c["check"] == "review_after_days")
        assert "since 2026-09-01" in rad["detail"]


class TestAlsoRunsIn:
    def _thin(self, radar, extra: str) -> dict:
        # One session that used the tool, twenty-five that did not: 1/26 = 3.8%, under
        # the 5% threshold that fires the thin-surface finding.
        radar.tool("pre-commit", cli_entry("pre-commit", '"pre-commit"', extra=extra))
        radar.session(
            "used",
            [bash("t1", "2026-08-05T10:00:00.000Z", "uv run pre-commit run --all-files")],
            mtime=epoch("2026-08-05"),
        )
        for i in range(25):
            radar.session(
                f"quiet{i:02d}",
                [note("2026-08-06T09:00:00.000Z"), note("2026-08-06T10:00:00.000Z")],
                mtime=epoch("2026-08-06"),
            )
        return radar.report("pre-commit", *WINDOW)

    def test_thin_surface_is_a_flag_when_nothing_else_is_declared(self, radar):
        doc = self._thin(radar, "")
        assert [f for f in doc["findings"] if f.startswith("[FLAG] pre-commit: thin")]

    def test_declared_invisible_surfaces_downgrade_the_flag_to_a_note(self, radar):
        # pre-commit measured 8/194 sessions while firing on every commit in nine
        # repos. The count is right; "an effect this rare cannot pay for its context
        # cost" is not a conclusion it supports.
        doc = self._thin(radar, 'also_runs_in = ["pre-commit hook"]\n')
        assert not [f for f in doc["findings"] if "thin opportunity surface" in f]
        hits = [f for f in doc["findings"] if "pre-commit hook" in f]
        assert len(hits) == 1
        assert hits[0].startswith("[NOTE]")
        assert "reach-frequency" in hits[0]


MCP_ENTRY = """\
name = "context7"
ring = "adopt"
artifact = "mcp-server"

[telemetry]
match = ["mcp__*__query-docs"]
since = "2026-08-01"
{extra}
"""


class TestOpportunity:
    """Zero use has three readings, and use alone separates none of them.

    The owner has a known salience problem - a tool present, applicable and never
    reached for - so a zero is never on its own an argument for moving a ring down. The
    opportunity matchers supply the denominator that use never had.
    """

    def _radar(self, radar, extra: str = "") -> None:
        radar.tool("context7", MCP_ENTRY.format(extra=extra))

    OPP = (
        'opportunity_match = ["WebFetch", "WebSearch"]\n'
        '[[history]]\ndate = "2026-08-01"\nring = "adopt"\n'
    )
    NO_OPP = '[[history]]\ndate = "2026-08-01"\nring = "adopt"\n'

    def test_an_undeclared_opportunity_reports_none_not_zero(self, radar):
        # "0 sessions had an opportunity" is a measurement; "nobody said what an
        # opportunity looks like" is not, and one reading as the other is how a zero
        # comes to look like evidence.
        self._radar(radar, self.NO_OPP)
        radar.session("s1", [note("2026-08-05T09:00:00.000Z")], mtime=epoch("2026-08-05"))
        m = radar.report("context7", *WINDOW)["metrics"]
        assert m["opportunity_sessions"] is None
        assert m["sessions_with_use_among_opportunity"] is None
        assert m["taken_ratio"] is None

    def test_a_session_that_had_the_chance_and_took_it(self, radar):
        self._radar(radar, self.OPP)
        radar.session(
            "took",
            [
                tool_use("t1", "WebFetch", "2026-08-05T10:00:00.000Z", url="https://x"),
                tool_use("t2", "mcp__abc__query-docs", "2026-08-05T10:01:00.000Z", q="x"),
            ],
            mtime=epoch("2026-08-05"),
        )
        m = radar.report("context7", *WINDOW)["metrics"]
        assert m["opportunity_sessions"] == 1
        assert m["sessions_with_use_among_opportunity"] == 1
        assert m["taken_ratio"] == 1.0

    def test_the_ratio_counts_sessions_that_had_the_chance_and_did_not(self, radar):
        self._radar(radar, self.OPP)
        radar.session(
            "took",
            [
                tool_use("t1", "WebSearch", "2026-08-05T10:00:00.000Z", query="x"),
                tool_use("t2", "mcp__abc__query-docs", "2026-08-05T10:01:00.000Z", q="x"),
            ],
            mtime=epoch("2026-08-05"),
        )
        for i in range(3):
            radar.session(
                f"missed{i}",
                [tool_use(f"w{i}", "WebFetch", "2026-08-06T10:00:00.000Z", url="https://y")],
                mtime=epoch("2026-08-06"),
            )
        # A session with neither: it is in the census denominator and must NOT be in the
        # opportunity one. That difference is the whole point of the number.
        radar.session("unrelated", [note("2026-08-07T10:00:00.000Z")], mtime=epoch("2026-08-07"))
        m = radar.report("context7", *WINDOW)["metrics"]
        assert m["sessions_in_window"] == 5
        assert m["opportunity_sessions"] == 4
        assert m["sessions_with_use_among_opportunity"] == 1
        assert m["taken_ratio"] == 0.25

    def test_the_opportunity_tools_actually_hit_are_listed(self, radar):
        # A matcher that never fires and an occasion that never arose produce the same
        # zero. Naming what did fire is what tells them apart.
        self._radar(radar, self.OPP)
        radar.session(
            "s1",
            [
                tool_use("t1", "WebFetch", "2026-08-05T10:00:00.000Z", url="https://x"),
                tool_use("t2", "WebFetch", "2026-08-05T10:01:00.000Z", url="https://y"),
                tool_use("t3", "WebSearch", "2026-08-05T10:02:00.000Z", query="z"),
            ],
            mtime=epoch("2026-08-05"),
        )
        m = radar.report("context7", *WINDOW)["metrics"]
        assert m["opportunity_tools"] == [
            {"tool": "WebFetch", "invocations": 2, "sessions": 1},
            {"tool": "WebSearch", "invocations": 1, "sessions": 1},
        ]

    def test_a_replayed_opportunity_is_counted_once(self, radar):
        # Same dedup rule as use: a resumed session replays its predecessor verbatim,
        # tool_use ids included, and counting both copies counts one chance twice.
        self._radar(radar, self.OPP)
        replayed = [tool_use("t1", "WebFetch", "2026-08-05T10:00:00.000Z", url="https://x")]
        radar.session("a-original", replayed, mtime=epoch("2026-08-05"))
        radar.session("b-resumed", list(replayed), mtime=epoch("2026-08-06"))
        m = radar.report("context7", *WINDOW)["metrics"]
        assert m["opportunity_invocations"] == 1
        assert m["opportunity_sessions"] == 1

    def test_the_prescan_does_not_hide_an_opportunity_only_session(self, radar):
        # The trap the module already met once with pre-commit: a transcript containing
        # only the OPPORTUNITY names holds none of the use needles, so a prescan filtered
        # on the use matchers alone never opens it - and the missing sessions are exactly
        # the population the ratio is about. Invisible rather than merely wrong.
        self._radar(radar, self.OPP)
        radar.session(
            "opportunity-only",
            [tool_use("t1", "WebFetch", "2026-08-05T10:00:00.000Z", url="https://x")],
            mtime=epoch("2026-08-05"),
        )
        m = radar.report("context7", *WINDOW)["metrics"]
        assert m["opportunity_sessions"] == 1
        assert m["invocations"] == 0

    def test_the_zero_use_note_names_the_three_readings(self, radar):
        self._radar(radar, self.NO_OPP)
        radar.session("s1", [note("2026-08-05T09:00:00.000Z")], mtime=epoch("2026-08-05"))
        doc = radar.report("context7", *WINDOW)
        zero = [f for f in doc["findings"] if "zero measured use" in f]
        assert len(zero) == 1
        for reading in (
            "the work never called for it",
            "not mounted or reachable",
            "was never reached for",
        ):
            assert reading in zero[0], zero[0]

    def test_the_zero_use_note_reports_the_opportunity_split(self, radar):
        self._radar(radar, self.OPP)
        for i in range(2):
            radar.session(
                f"missed{i}",
                [tool_use(f"w{i}", "WebFetch", "2026-08-06T10:00:00.000Z", url="https://y")],
                mtime=epoch("2026-08-06"),
            )
        doc = radar.report("context7", *WINDOW)
        zero = next(f for f in doc["findings"] if "zero measured use" in f)
        assert "2 session(s) had an opportunity" in zero
        assert "0 took it" in zero

    def test_also_runs_in_reaches_the_zero_use_note(self, radar):
        # It was consulted only in the thin-surface branch, so an entry that runs
        # ENTIRELY where no transcript can see it - the strongest case for the
        # qualification - got the unqualified finding.
        radar.tool(
            "pre-commit",
            cli_entry("pre-commit", '"pre-commit"', extra='also_runs_in = ["pre-commit hook"]\n'),
        )
        radar.session("s1", [note("2026-08-05T09:00:00.000Z")], mtime=epoch("2026-08-05"))
        doc = radar.report("pre-commit", *WINDOW)
        zero = next(f for f in doc["findings"] if "zero measured use" in f)
        assert "pre-commit hook" in zero
        assert "reach, not use" in zero

    def test_the_markdown_carries_the_section(self, radar):
        self._radar(radar, self.OPP)
        radar.session(
            "s1",
            [tool_use("t1", "WebFetch", "2026-08-05T10:00:00.000Z", url="https://x")],
            mtime=epoch("2026-08-05"),
        )
        radar.run("context7", *WINDOW)
        md = (radar.root / "field" / "context7" / "2026-08-01_2026-08-31.md").read_text(
            encoding="utf-8"
        )
        assert "## Opportunity vs use (salience)" in md
        assert "| sessions with an opportunity | 1 |" in md
        assert "`WebFetch`" in md


def arch(day: str, *, tool: str = "Bash", exe=(), skill=None, first_seen: bool = True) -> dict:
    """One row shaped like the archive's derived invocation table."""
    row = {
        "session_id": "s",
        "slug": "p",
        "is_sidechain": False,
        "timestamp": f"{day}T10:00:00.000Z",
        "tool": tool,
        "first_seen": first_seen,
    }
    if exe:
        row["exe"] = list(exe)
    if skill:
        row["skill"] = skill
    return row


class TestReconcile:
    def _setup(self, radar, rows, *, generated_at="2026-09-01T00:00:00Z"):
        radar.write_environment(with_archive=True)
        radar.archive_rows(rows, generated_at=generated_at)
        radar.tool("ruff", cli_entry("ruff", '"ruff"'))

    def test_agreement_is_reported_per_matcher_kind(self, radar):
        self._setup(radar, [arch("2026-08-05", exe=["uv", "ruff"])])
        radar.session(
            "s1",
            [bash("t1", "2026-08-05T10:00:00.000Z", "uv run ruff check .")],
            mtime=epoch("2026-08-05"),
        )
        doc = radar.report("ruff", "--reconcile", *WINDOW)
        rec = doc["reconcile"]
        assert rec["generated_at"] == "2026-09-01T00:00:00Z"
        assert rec["age_days"] is not None
        assert rec["kinds"] == [
            {
                "kind": "command",
                "ours": 1,
                "archive": 1,
                "delta": 0,
                "relative": 0.0,
                "comparable": True,
            }
        ]
        assert not [f for f in doc["findings"] if "reconcile drift" in f]

    def test_disagreement_above_the_tolerance_warns(self, radar):
        # Ten rows in the archive, one invocation on disk: the two parsers cannot both
        # be right, and the point of the flag is that neither notices alone.
        self._setup(radar, [arch("2026-08-05", exe=["ruff"]) for _ in range(10)])
        radar.session(
            "s1",
            [bash("t1", "2026-08-05T10:00:00.000Z", "uv run ruff check .")],
            mtime=epoch("2026-08-05"),
        )
        doc = radar.report("ruff", "--reconcile", *WINDOW)
        assert doc["reconcile"]["kinds"][0] == {
            "kind": "command",
            "ours": 1,
            "archive": 10,
            "delta": -9,
            "relative": 0.9,
            "comparable": True,
        }
        warns = [f for f in doc["findings"] if "reconcile drift on `command`" in f]
        assert len(warns) == 1 and warns[0].startswith("[WARN]")
        assert "2026-09-01" in warns[0]

    def test_replayed_rows_in_the_archive_are_not_counted(self, radar):
        # The archive marks replayed copies. Counting them would compare our
        # de-duplicated total against its raw one and report drift that is not drift.
        self._setup(
            radar,
            [
                arch("2026-08-05", exe=["ruff"]),
                arch("2026-08-05", exe=["ruff"], first_seen=False),
            ],
        )
        radar.session(
            "s1",
            [bash("t1", "2026-08-05T10:00:00.000Z", "uv run ruff check .")],
            mtime=epoch("2026-08-05"),
        )
        doc = radar.report("ruff", "--reconcile", *WINDOW)
        assert doc["reconcile"]["kinds"][0]["archive"] == 1

    def test_only_declared_matcher_kinds_are_compared(self, radar):
        self._setup(
            radar,
            [arch("2026-08-05", exe=["ruff"]), arch("2026-08-05", tool="Skill", skill="widget:x")],
        )
        radar.session(
            "s1",
            [bash("t1", "2026-08-05T10:00:00.000Z", "uv run ruff check .")],
            mtime=epoch("2026-08-05"),
        )
        doc = radar.report("ruff", "--reconcile", *WINDOW)
        assert [k["kind"] for k in doc["reconcile"]["kinds"]] == ["command"]

    def test_the_archive_is_counted_under_the_same_module_spelling(self, radar):
        # The archive resolves the same chain, so a module-form call is `pre_commit`
        # in its table too. Counting its rows under the DECLARED glob while counting
        # ours under the expanded one would make the two scanners disagree by
        # construction, and a drift warning that says nothing about either parser is
        # the one kind of finding this cross-check cannot afford.
        radar.write_environment(with_archive=True)
        radar.archive_rows(
            [arch("2026-08-05", exe=["uv", "python", "pre_commit"])],
            generated_at="2026-09-01T00:00:00Z",
        )
        radar.tool("pre-commit", cli_entry("pre-commit", '"pre-commit"'))
        radar.session(
            "s1",
            [bash("t1", "2026-08-05T10:00:00.000Z", "uv run python -m pre_commit run -a")],
            mtime=epoch("2026-08-05"),
        )
        doc = radar.report("pre-commit", "--reconcile", *WINDOW)
        kind = doc["reconcile"]["kinds"][0]
        assert (kind["ours"], kind["archive"]) == (1, 1)
        assert not [f for f in doc["findings"] if "reconcile drift" in f]

    def test_a_missing_store_is_a_note_not_a_failure(self, radar):
        radar.tool("ruff", cli_entry("ruff", '"ruff"'))
        radar.session("s1", [note("2026-08-05T10:00:00.000Z")], mtime=epoch("2026-08-05"))
        doc = radar.report("ruff", "--reconcile", *WINDOW)
        assert doc["reconcile"] is None
        assert [f for f in doc["findings"] if f.startswith("[NOTE]") and "archive_store" in f]

    def test_reconcile_is_off_unless_asked_for(self, radar):
        self._setup(radar, [arch("2026-08-05", exe=["ruff"]) for _ in range(10)])
        radar.session(
            "s1",
            [bash("t1", "2026-08-05T10:00:00.000Z", "uv run ruff check .")],
            mtime=epoch("2026-08-05"),
        )
        doc = radar.report("ruff", *WINDOW)
        assert doc["reconcile"] is None
        assert not [f for f in doc["findings"] if "reconcile" in f]


class TestOutDir:
    """`field/` is tracked, so a verification run must be able to write somewhere else.

    Checking a number quoted in the docs means re-running the window the report was
    generated from - which, without this, rewrites the exact artefact the check compares
    against. The verification then confirms the file it just wrote.
    """

    def test_reports_go_where_they_are_asked_to(self, radar, tmp_path):
        elsewhere = tmp_path / "scratch-reports"
        radar.tool("ruff", cli_entry("ruff", '"ruff"'))
        radar.session(
            "s1",
            [bash("t1", "2026-08-05T10:00:00.000Z", "uv run ruff check .")],
            mtime=epoch("2026-08-05"),
        )
        p = radar.run("ruff", "--out-dir", str(elsewhere), *WINDOW)
        assert p.returncode == 0, p.stdout + p.stderr
        assert (elsewhere / "ruff" / "2026-08-01_2026-08-31.json").is_file()
        assert (elsewhere / "ruff" / "2026-08-01_2026-08-31.md").is_file()
        assert not (radar.root / "field").exists(), "the tracked directory was touched"

    def test_all_honours_it_too(self, radar, tmp_path):
        elsewhere = tmp_path / "scratch-board"
        radar.tool("ruff", cli_entry("ruff", '"ruff"'))
        radar.tool("widget", cli_entry("widget", '"widget"'))
        radar.session("s1", [note("2026-08-05T10:00:00.000Z")], mtime=epoch("2026-08-05"))
        radar.run("--all", "--out-dir", str(elsewhere), *WINDOW)
        assert (elsewhere / "ruff" / "2026-08-01_2026-08-31.md").is_file()
        assert (elsewhere / "widget" / "2026-08-01_2026-08-31.md").is_file()
        assert not (radar.root / "field").exists()


class TestAll:
    def _board(self, radar):
        radar.tool("ruff", cli_entry("ruff", '"ruff"'))
        radar.tool("widget", cli_entry("widget", '"widget"', ring="own"))
        radar.tool("zizmor", cli_entry("zizmor", '"zizmor"', ring="observe"))
        radar.tool("aider", entry("aider", ring="observe"))  # no matcher at all
        radar.session(
            "s1",
            [
                bash("t1", "2026-08-05T10:00:00.000Z", "uv run ruff check ."),
                tool_result("t1", "2026-08-05T10:00:01.000Z", is_error=True),
                bash("t2", "2026-08-05T10:02:00.000Z", "uvx widget check"),
                bash("t3", "2026-08-05T10:03:00.000Z", "zizmor ."),
            ],
            mtime=epoch("2026-08-05"),
        )

    def test_runs_every_active_entry_with_a_matcher(self, radar):
        self._board(radar)
        p = radar.run("--all", *WINDOW)
        assert p.returncode == 0, p.stdout + p.stderr
        table = p.stdout[p.stdout.index("tool") :]
        assert "widget" in table and "ruff" in table
        # observe is inactive by default; an entry with no matcher is never selected.
        assert "zizmor" not in table
        assert "aider" not in table
        assert "2 entries with a matcher" in p.stdout

    def test_include_inactive_adds_the_demoted_entries(self, radar):
        self._board(radar)
        p = radar.run("--all", "--include-inactive", *WINDOW)
        assert p.returncode == 0, p.stdout + p.stderr
        assert "zizmor" in p.stdout[p.stdout.index("tool") :]
        assert "3 entries with a matcher" in p.stdout

    def test_the_summary_carries_the_deciding_columns(self, radar):
        self._board(radar)
        p = radar.run("--all", *WINDOW)
        header = next(ln for ln in p.stdout.splitlines() if ln.startswith("tool"))
        assert header.split() == ["tool", "ring", "inv", "sessions", "err", "findings"]
        row = next(ln for ln in p.stdout.splitlines() if ln.startswith("ruff"))
        assert row.split()[:5] == ["ruff", "adopt", "1", "1/1", "1"]

    def test_each_entry_still_writes_its_own_report(self, radar):
        self._board(radar)
        radar.run("--all", *WINDOW)
        for name in ("ruff", "widget"):
            out = radar.root / "field" / name / "2026-08-01_2026-08-31.md"
            assert out.is_file(), f"{name} wrote no report"

    def test_json_with_all_is_refused_rather_than_ignored(self, radar):
        # Silently ignoring the flag hands a caller an empty parse instead of an error.
        self._board(radar)
        p = radar.run("--all", "--json", *WINDOW)
        assert p.returncode == 2
        assert "read field/<tool>/*.json" in p.stdout

    def test_naming_a_tool_and_all_together_is_refused(self, radar):
        self._board(radar)
        p = radar.run("ruff", "--all", *WINDOW)
        assert p.returncode == 2
        assert "not both, not neither" in p.stdout


class TestScriptIdentity:
    """A `cli` that ships as a python script has no name but its filename."""

    def test_a_script_run_through_the_interpreter_counts(self, radar):
        radar.tool("archive", cli_entry("archive", '"archive.py"'))
        radar.session(
            "s1",
            [bash("t1", "2026-08-05T10:00:00.000Z", "uv run python archive.py capture")],
            mtime=epoch("2026-08-05"),
        )
        doc = radar.report("archive", *WINDOW)
        assert doc["metrics"]["invocations"] == 1
        assert doc["metrics"]["per_tool"][0]["tool"] == "cmd:archive.py"

    def test_a_script_named_as_an_argument_to_another_tool_does_not(self, radar):
        # The substring trap, in its last hiding place: naming the file is not running
        # it. Every one of these resolves to ruff / git / cat, not to python.
        radar.tool("archive", cli_entry("archive", '"archive.py"'))
        radar.session(
            "s1",
            [
                bash("t1", "2026-08-05T10:00:00.000Z", "ruff check archive.py"),
                bash("t2", "2026-08-05T10:01:00.000Z", "git add archive.py"),
                bash("t3", "2026-08-05T10:02:00.000Z", "cat archive.py | head -20"),
            ],
            mtime=epoch("2026-08-05"),
        )
        doc = radar.report("archive", *WINDOW)
        assert doc["metrics"]["invocations"] == 0

    def test_a_different_script_with_a_similar_name_does_not_match(self, radar):
        radar.tool("archive", cli_entry("archive", '"archive.py"'))
        radar.session(
            "s1",
            [
                bash(
                    "t1",
                    "2026-08-05T10:00:00.000Z",
                    "uv run python scripts/make_dev_archive.py --dry-run",
                )
            ],
            mtime=epoch("2026-08-05"),
        )
        doc = radar.report("archive", *WINDOW)
        assert doc["metrics"]["invocations"] == 0

    def test_executable_matchers_are_unaffected(self, radar):
        # The addition must be inert for every existing entry: a script identity always
        # carries its `.py`, so a bare executable glob can never collide with one.
        radar.tool("ruff", cli_entry("ruff", '"ruff"'))
        radar.session(
            "s1",
            [
                bash("t1", "2026-08-05T10:00:00.000Z", "uv run ruff check ."),
                bash("t2", "2026-08-05T10:01:00.000Z", "uv run python ruff.py"),
            ],
            mtime=epoch("2026-08-05"),
        )
        doc = radar.report("ruff", *WINDOW)
        assert doc["metrics"]["invocations"] == 1

    def test_a_script_glob_beside_an_executable_glob_is_compared_too(self, radar):
        # Until the archive's 0.3.0 a script-shaped glob had no row on the archive's side
        # (its scanner stopped at the interpreter), so it was carved out of the
        # cross-check per glob and named as excluded. Both scanners now name the script
        # an interpreter runs, so an entry declaring ["archive.py", "just"] is compared
        # under BOTH globs and nothing is excluded.
        radar.write_environment(with_archive=True)
        radar.archive_rows(
            [
                arch("2026-08-05", exe=["just"]),
                arch("2026-08-05", exe=["uv", "python", "archive.py"]),
            ],
            generated_at="2026-09-01T00:00:00Z",
        )
        radar.tool("archive", cli_entry("archive", '"archive.py", "just"'))
        radar.session(
            "s1",
            [
                bash("t1", "2026-08-05T10:00:00.000Z", "just build"),
                bash("t2", "2026-08-05T10:01:00.000Z", "uv run python archive.py capture"),
            ],
            mtime=epoch("2026-08-05"),
        )
        doc = radar.report("archive", "--reconcile", *WINDOW)
        kind = doc["reconcile"]["kinds"][0]
        assert kind["kind"] == "command"
        assert kind["comparable"] is True
        assert (kind["ours"], kind["archive"]) == (2, 2)
        assert "excluded_globs" not in kind
        assert "excluded_because" not in kind
        assert not [f for f in doc["findings"] if "reconcile drift" in f]
        assert doc["metrics"]["invocations"] == 2

    def test_a_script_matcher_is_compared_against_the_archive_script_names(self, radar):
        # The archive's scanner (0.3.0 and later) records the script an interpreter
        # runs, so a script-named glob has a row on both sides and is compared like any
        # other - no incomparability by construction any more.
        radar.write_environment(with_archive=True)
        radar.archive_rows(
            [arch("2026-08-05", exe=["uv", "python", "archive.py"])],
            generated_at="2026-09-01T00:00:00Z",
        )
        radar.tool("archive", cli_entry("archive", '"archive.py"'))
        radar.session(
            "s1",
            [bash("t1", "2026-08-05T10:00:00.000Z", "uv run python archive.py capture")],
            mtime=epoch("2026-08-05"),
        )
        doc = radar.report("archive", "--reconcile", *WINDOW)
        kind = doc["reconcile"]["kinds"][0]
        assert kind["kind"] == "command"
        assert (kind["ours"], kind["archive"]) == (1, 1)
        assert kind["comparable"] is True
        assert "incomparable_because" not in kind
        assert not [f for f in doc["findings"] if "reconcile drift" in f]

    def test_a_table_derived_before_script_identity_reads_as_drift(self, radar):
        # A derived table from before the archive named scripts still has only the
        # interpreter in its rows. That is no longer a structural gap to be excused: it
        # is a stale table, and the right signal is drift, because the fix is to
        # re-derive. Excusing it would hide exactly the staleness --reconcile exists to
        # surface.
        radar.write_environment(with_archive=True)
        radar.archive_rows(
            [arch("2026-08-05", exe=["uv", "python"])], generated_at="2026-09-01T00:00:00Z"
        )
        radar.tool("archive", cli_entry("archive", '"archive.py"'))
        radar.session(
            "s1",
            [bash("t1", "2026-08-05T10:00:00.000Z", "uv run python archive.py capture")],
            mtime=epoch("2026-08-05"),
        )
        doc = radar.report("archive", "--reconcile", *WINDOW)
        kind = doc["reconcile"]["kinds"][0]
        assert kind["comparable"] is True
        assert (kind["ours"], kind["archive"]) == (1, 0)
        assert [f for f in doc["findings"] if "reconcile drift" in f]


def slug_of(path: Path) -> str:
    """The project-folder name Claude Code gives a working directory.

    Every character outside ASCII letters and digits becomes `-`, so a session run under
    the home directory is filed under a name that starts with the home in that spelling -
    `C:/Users/<account>/Documents/p` becomes `C--Users-<account>-Documents-p`.
    """
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


class TestTheGenerator:
    """A written report names the command that wrote it, as a reader can run it."""

    def test_the_written_report_names_radar_field(self, radar):
        # `generated_by` was the path of the script the engine ran as before it was
        # packaged. The value a reader keys on is now the verb, so a report written from
        # here on is told apart from an older one by that value alone.
        radar.tool("ruff", cli_entry("ruff", '"ruff"'))
        radar.session(
            "s1",
            [
                bash("t1", "2026-08-05T10:00:00.000Z", "ruff check ."),
                tool_result("t1", "2026-08-05T10:00:01.000Z"),
            ],
            mtime=epoch("2026-08-05"),
        )
        p = radar.run("ruff", *WINDOW)
        assert p.returncode == 0, p.stdout + p.stderr
        base = radar.root / "field" / "ruff"
        doc = json.loads((base / "2026-08-01_2026-08-31.json").read_text(encoding="utf-8"))
        md = (base / "2026-08-01_2026-08-31.md").read_text(encoding="utf-8")
        assert doc["generated_by"] == "radar field"
        assert "generated by `radar field` - do not hand-edit" in md


class TestHomeRedaction:
    """The machine's home directory never reaches a written report.

    `field/` is tracked, and a report carried the home in three places nobody wrote by
    hand: `claude_home`, the archive store under `--reconcile`, and every project slug,
    which Claude Code derives from the working directory. Measured on one catalogue, all
    88 committed reports named the account that way. The written artefacts carry `~`
    instead - `~-` at the head of a slug - and `--json` to stdout keeps the real paths,
    the same split the scoped-name redaction makes.
    """

    STEM = "2026-08-01_2026-08-31"

    def _written(self, radar, tool: str) -> tuple[str, str]:
        base = radar.root / "field" / tool
        md = (base / f"{self.STEM}.md").read_text(encoding="utf-8")
        js = (base / f"{self.STEM}.json").read_text(encoding="utf-8")
        return md, js

    def _board(self, radar, *, with_archive: bool = False) -> None:
        radar.user_home = radar.root
        if with_archive:
            radar.write_environment(with_archive=True)
            radar.archive_rows(
                [arch("2026-08-05", exe=["ruff"])], generated_at="2026-09-01T00:00:00Z"
            )
        radar.tool("ruff", cli_entry("ruff", '"ruff"'))
        radar.session(
            "s1",
            [
                bash("t1", "2026-08-05T10:00:00.000Z", "ruff check ."),
                tool_result("t1", "2026-08-05T10:00:01.000Z"),
            ],
            slug=slug_of(radar.root) + "-Documents-p",
            mtime=epoch("2026-08-05"),
        )

    def test_a_slug_under_the_home_is_written_with_the_placeholder(self, radar):
        self._board(radar)
        p = radar.run("ruff", *WINDOW)
        assert p.returncode == 0, p.stdout + p.stderr
        md, js = self._written(radar, "ruff")
        doc = json.loads(js)
        assert [i["slug"] for i in doc["invocations"]] == ["~-Documents-p"]
        assert doc["metrics"]["slugs"] == [{"slug": "~-Documents-p", "invocations": 1}]
        assert "| `~-Documents-p` | 1 |" in md
        for text in (md, js):
            assert slug_of(radar.root) not in text

    def test_a_path_under_the_home_is_written_with_the_placeholder(self, radar):
        self._board(radar, with_archive=True)
        p = radar.run("ruff", "--reconcile", *WINDOW)
        assert p.returncode == 0, p.stdout + p.stderr
        md, js = self._written(radar, "ruff")
        doc = json.loads(js)
        claude_home, store = str(Path("~") / "home"), str(Path("~") / "archive-store")
        assert doc["claude_home"] == claude_home
        assert doc["reconcile"]["store"] == store
        assert f"claude_home: `{claude_home}`" in md
        assert f"- archive store: `{store}`" in md
        for text in (md, js):
            for spelling in (str(radar.root), radar.root.as_posix()):
                assert spelling not in text
                assert json.dumps(spelling)[1:-1] not in text

    def test_nothing_but_the_home_changes(self, radar):
        # The written JSON against the document `--json` prints from the SAME run, which
        # keeps the real paths: once the home is mapped by hand, the two must be equal to
        # the byte. A slug outside the home rides along to show it is left alone.
        self._board(radar, with_archive=True)
        radar.session(
            "s2",
            [bash("t2", "2026-08-06T10:00:00.000Z", "ruff format .")],
            mtime=epoch("2026-08-06"),
        )
        p = radar.run("ruff", "--json", "--reconcile", *WINDOW)
        assert p.returncode == 0, p.stdout + p.stderr
        real = json.dumps(json.loads(p.stdout), indent=2) + "\n"
        escaped, slug = json.dumps(str(radar.root))[1:-1], slug_of(radar.root)
        # Without the home in the unredacted document the comparison would prove nothing.
        assert escaped in real and slug in real
        assert '"slug": "C--Users-x-Documents-p"' in real
        _md, js = self._written(radar, "ruff")
        assert js == real.replace(escaped, "~").replace(slug, "~")

    def test_a_declared_extra_home_is_written_with_the_placeholder(self, radar):
        # A report mined on this machine can still carry ANOTHER machine's home: sessions
        # synced from it keep their slugs. `redact.local.toml` declares that home, and the
        # written report must map it the way it maps this machine's own.
        self._board(radar)
        other = radar.root.parent / "other.account"
        (radar.root / "redact.local.toml").write_text(
            f"homes = [{json.dumps(other.as_posix())}]\n", encoding="utf-8"
        )
        radar.session(
            "s3",
            [bash("t3", "2026-08-07T10:00:00.000Z", "ruff check .")],
            slug=slug_of(other) + "-Documents-q",
            mtime=epoch("2026-08-07"),
        )
        p = radar.run("ruff", *WINDOW)
        assert p.returncode == 0, p.stdout + p.stderr
        md, js = self._written(radar, "ruff")
        slugs = sorted(i["slug"] for i in json.loads(js)["invocations"])
        assert slugs == ["~-Documents-p", "~-Documents-q"], slugs
        for text in (md, js):
            assert slug_of(other) not in text

    def test_stdout_keeps_the_real_paths(self, radar):
        self._board(radar)
        doc = radar.report("ruff", *WINDOW)
        assert doc["claude_home"] == str(radar.home)
        assert doc["invocations"][0]["slug"] == slug_of(radar.root) + "-Documents-p"
