"""`snapshot.py --help` must never start a refresh.

`main()` had no argument parser. `--help` was not a flag the script recognised, so it
fell straight through to `begin_command()` and then into the loop that shells out to
`gh api` and hits the network for every tool in the catalogue, writing a fresh
`snapshots/<today>.json` on the way.

`begin_command` is the function monkeypatched to raise below: it is the first
side-effecting call `main()` makes, so proving it was never reached proves the network
calls and the disk write after it never ran either. On the pre-change code, `--help`
reaches it exactly like no arguments would - there is no parser to stop it, print usage,
or exit 0 first - so these tests fail there.
"""

from __future__ import annotations

import pytest

from stack_radar import snapshot


def _forbid_begin_command(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*args: object, **kwargs: object) -> None:
        raise AssertionError(
            "begin_command() ran - snapshot.main() did work before parsing its arguments"
        )

    monkeypatch.setattr(snapshot, "begin_command", _boom)


def test_help_exits_zero_prints_usage_and_never_touches_begin_command(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _forbid_begin_command(monkeypatch)
    monkeypatch.setattr("sys.argv", ["snapshot.py", "--help"])

    with pytest.raises(SystemExit) as excinfo:
        snapshot.main()

    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "usage" in out.lower()


def test_an_unknown_argument_exits_2_without_touching_begin_command(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _forbid_begin_command(monkeypatch)
    monkeypatch.setattr("sys.argv", ["snapshot.py", "--not-a-real-flag"])

    with pytest.raises(SystemExit) as excinfo:
        snapshot.main()

    assert excinfo.value.code == 2


def test_data_root_still_reaches_begin_command_as_an_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--data-root` keeps resolving the same way it always did - through the override
    argument `begin_command` accepts - rather than falling back to the resolver's own
    argv sniffing now that a real parser declares the flag."""
    seen: dict[str, object] = {}

    def _record(override: str | None = None, **kwargs: object):
        seen["override"] = override
        raise SystemExit(0)

    monkeypatch.setattr(snapshot, "begin_command", _record)
    monkeypatch.setattr("sys.argv", ["snapshot.py", "--data-root", "C:/somewhere"])

    with pytest.raises(SystemExit):
        snapshot.main()

    assert seen["override"] == "C:/somewhere"
