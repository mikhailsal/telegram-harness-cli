import subprocess
from pathlib import Path

import pytest

from quality.process import (
    Command,
    InvalidCommandError,
    ProcessTimeoutError,
    run_attached,
    runner,
    sanitized,
)


def sh(script: str, cwd: Path, *, timeout: float = 10, stdin: bytes | None = None) -> Command:
    return Command(("sh", "-c", script), cwd=cwd, timeout=timeout, env=sanitized(), stdin=stdin)


def test_output_goes_to_the_callers_streams(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    print("before", flush=False)
    assert run_attached(sh("echo out; echo err >&2; exit 3", tmp_path)) == 3
    captured = capfd.readouterr()
    assert (captured.out, captured.err) == ("before\nout\n", "err\n")


def test_stdin_is_passed_and_otherwise_empty(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    assert run_attached(sh("cat", tmp_path, stdin=b"given")) == 0
    assert run_attached(sh("cat", tmp_path)) == 0
    assert capfd.readouterr().out == "given"


def test_invalid_commands_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(InvalidCommandError):
        run_attached(sh("true", tmp_path, timeout=0))


def test_timeout_terminates_the_child(tmp_path: Path) -> None:
    with pytest.raises(ProcessTimeoutError, match=r"timed out after 0\.2s"):
        run_attached(sh("sleep 30", tmp_path, timeout=0.2))


def test_interrupt_terminates_the_child(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    terminated: list[subprocess.Popen[bytes]] = []
    original = runner.terminate

    def interrupted(*_args: object, **_kwargs: object) -> tuple[bytes, bytes]:
        raise KeyboardInterrupt

    def recording_terminate(process: subprocess.Popen[bytes]) -> None:
        terminated.append(process)
        original(process)

    monkeypatch.setattr(subprocess.Popen, "communicate", interrupted)
    monkeypatch.setattr(runner, "terminate", recording_terminate)
    with pytest.raises(KeyboardInterrupt):
        run_attached(sh("sleep 30", tmp_path))
    assert len(terminated) == 1
    assert terminated[0].returncode is not None
