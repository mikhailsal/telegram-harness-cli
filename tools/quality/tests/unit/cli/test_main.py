import argparse
import runpy
import sys

import pytest

from quality import __version__
from quality.cli import explain_cmd
from quality.cli.main import build_parser, command_table, main


def test_version_is_printed(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        main(["--version"])
    assert raised.value.code == 0
    assert capsys.readouterr().out == f"quality {__version__}\n"


def test_help_documents_exit_codes(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["--help"])
    out = capsys.readouterr().out
    assert "Exit codes: 0 pass, 1 violations" in out
    assert "explain" in out
    assert "policy" in out


@pytest.mark.parametrize("argv", [[], ["unknown"], ["policy"], ["explain", "a.rule", "--list"]])
def test_usage_errors_exit_with_two(argv: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        main(argv)
    assert raised.value.code == 2
    assert "usage: quality" in capsys.readouterr().err


def fail_with(error: BaseException) -> None:
    raise error


def test_internal_errors_exit_with_four(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    message = "boom"
    monkeypatch.setattr(explain_cmd, "build_registry", lambda: fail_with(RuntimeError(message)))
    assert main(["explain", "--list"]) == 4
    err = capsys.readouterr().err
    assert "RuntimeError: boom" in err
    assert "internal error (exit 4)" in err


def test_interrupts_exit_with_130(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(explain_cmd, "build_registry", lambda: fail_with(KeyboardInterrupt()))
    assert main(["explain", "--list"]) == 130
    assert capsys.readouterr().err == "interrupted\n"


def test_parser_requires_a_command() -> None:
    parser = build_parser()
    assert isinstance(parser, argparse.ArgumentParser)
    assert parser.prog == "quality"


def test_module_entry_point(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["quality", "explain", "--list"])
    with pytest.raises(SystemExit) as raised:
        runpy.run_module("quality", run_name="__main__", alter_sys=True)
    assert raised.value.code == 0


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["explain", "--help"], "2 when it is unknown"),
        (["policy", "validate", "--help"], "2 when the policy is missing or invalid"),
        (["policy", "schema", "--help"], "exit 1 when stale"),
    ],
)
def test_subcommand_help_states_exit_codes(
    argv: list[str], expected: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit):
        main(argv)
    assert expected in " ".join(capsys.readouterr().out.split())


def test_the_command_table_matches_the_parser(capsys: pytest.CaptureFixture[str]) -> None:
    table = command_table()
    assert set(table) == {
        "audit",
        "bootstrap",
        "check",
        "ci",
        "component",
        "doctor",
        "explain",
        "fix",
        "gate",
        "inventory",
        "links",
        "policy",
        "scenarios",
        "self-test",
        "template",
        "test",
        "tools",
    }
    parser = build_parser()
    assert parser.parse_args(["explain", "--list"]).command_table == table
    for name, actions in table.items():
        for argv in ([name, "--help"], *([name, action, "--help"] for action in actions)):
            with pytest.raises(SystemExit) as raised:
                parser.parse_args(argv)
            assert raised.value.code == 0
    capsys.readouterr()
    for name in ("ci", "component", "policy", "template", "tools"):
        with pytest.raises(SystemExit) as raised:
            parser.parse_args([name, "unknown"])
        assert raised.value.code == 2
