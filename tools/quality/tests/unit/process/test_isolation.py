import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from quality.process import Command, Completed, ProcessTimeoutError, isolation, run, sanitized
from quality.process.isolation import STUB, contained, isolated, unavailable

# Loopback must answer and a documentation address (TEST-NET-1) must be unreachable.
NETWORK = """\
import os, socket
with socket.create_server(("127.0.0.1", 0)) as server:
    with socket.create_connection(server.getsockname(), timeout=5):
        print("loopback", os.getuid())
try:
    socket.create_connection(("192.0.2.1", 9), timeout=5)
except OSError as error:
    print("unreachable", error.errno)
"""


@pytest.fixture(autouse=True)
def fresh_probe() -> Iterator[None]:
    unavailable.cache_clear()
    yield
    unavailable.cache_clear()


def probe_result(monkeypatch: pytest.MonkeyPatch, returncode: int, stderr: bytes) -> None:
    def fake(command: Command) -> Completed:
        assert command.argv == isolated(())
        return Completed(command.argv, returncode, b"", stderr, 0.0)

    monkeypatch.setattr(isolation, "run", fake)


def test_commands_start_behind_the_stub_on_the_isolated_interpreter() -> None:
    assert isolated(("pytest", "-q")) == (sys.executable, "-I", "-c", STUB, "pytest", "-q")


def test_a_working_probe_isolates_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    probe_result(monkeypatch, 0, b"")
    assert unavailable() is None
    assert contained(("pytest",)) == (isolated(("pytest",)), None)


def test_a_failing_probe_says_why_and_leaves_commands_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe_result(monkeypatch, 1, b"network isolation is unavailable: [Errno 1] denied\n")
    reason = "network isolation is unavailable: [Errno 1] denied"
    assert unavailable() == reason
    assert contained(("pytest",)) == (("pytest",), reason)


def test_a_silent_failing_probe_reports_its_status(monkeypatch: pytest.MonkeyPatch) -> None:
    probe_result(monkeypatch, 9, b"")
    assert unavailable() == "the probe exited with status 9"


def test_a_probe_that_cannot_run_reports_the_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def hang(command: Command) -> Completed:
        raise ProcessTimeoutError(command.argv, "timed out after 30s")

    monkeypatch.setattr(isolation, "run", hang)
    reason = unavailable()
    assert reason is not None
    assert reason.startswith("timed out after 30s")


def test_isolated_commands_reach_loopback_only(tmp_path: Path) -> None:
    command = isolated((sys.executable, "-I", "-c", NETWORK))
    completed = run(Command(command, cwd=tmp_path, timeout=30, env=sanitized()))
    reason = unavailable()
    if reason is None:
        assert completed.stdout_text().splitlines() == [
            f"loopback {os.getuid()}",
            "unreachable 101",
        ], completed.stderr_text()
    else:
        assert completed.returncode == 1
        assert "network isolation is unavailable" in completed.stderr_text()


def test_an_isolated_command_that_cannot_start_fails_with_its_name(tmp_path: Path) -> None:
    missing = str(tmp_path / "missing")
    completed = run(Command(isolated((missing,)), cwd=tmp_path, timeout=30, env=sanitized()))
    assert completed.returncode == 1
    assert ("cannot run " + missing if unavailable() is None else "unavailable") in (
        completed.stderr_text()
    )
