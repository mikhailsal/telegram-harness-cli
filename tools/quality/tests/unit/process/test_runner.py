import dataclasses
import errno
import os
import signal
import subprocess
import time
from pathlib import Path

import pytest

from quality.process import (
    Command,
    Completed,
    ExecutableNotFoundError,
    InvalidCommandError,
    ProcessTimeoutError,
    run,
    runner,
    sanitized,
)


def sh(
    script: str,
    cwd: Path,
    *,
    timeout: float = 10,
    stdin: bytes | None = None,
    output_limit: int = runner.DEFAULT_OUTPUT_LIMIT,
) -> Command:
    return Command(
        ("sh", "-c", script),
        cwd=cwd,
        timeout=timeout,
        env=sanitized(),
        stdin=stdin,
        output_limit=output_limit,
    )


def wait_gone(pid: int) -> bool:
    """Wait until a process no longer exists or is an unreaped zombie.

    A killed process can take seconds to exit on a loaded machine. The children
    sleep far longer than this bound, so one that was never killed cannot pass.
    """
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            stat = Path(f"/proc/{pid}/stat").read_text()
        except FileNotFoundError, ProcessLookupError:
            return True
        if stat.rsplit(")", 1)[1].split()[0] == "Z":
            return True
        time.sleep(0.05)
    return False


def test_successful_command_captures_output(tmp_path: Path) -> None:
    result = run(sh("echo out; echo err >&2; pwd", tmp_path))
    assert result.ok
    assert result.returncode == 0
    assert result.stdout_text() == f"out\n{tmp_path}\n"
    assert result.stderr_text() == "err\n"
    assert not result.stdout_truncated
    assert result.duration >= 0


def test_failing_command_returns_its_status(tmp_path: Path) -> None:
    result = run(sh("exit 3", tmp_path))
    assert not result.ok
    assert result.returncode == 3


def test_stdin_is_passed_and_otherwise_empty(tmp_path: Path) -> None:
    assert run(sh("cat", tmp_path, stdin=b"data")).stdout == b"data"
    assert run(sh("cat", tmp_path)).stdout == b""


def test_output_is_truncated_at_the_limit(tmp_path: Path) -> None:
    result = run(sh("head -c 100 /dev/zero; head -c 50 /dev/zero >&2", tmp_path, output_limit=10))
    assert result.stdout == b"\0" * 10
    assert result.stdout_truncated
    assert result.stderr_truncated


def test_invalid_utf8_output_is_replaced_when_decoded() -> None:
    result = Completed(("x",), 0, b"\xffa", b"\xfeb", 0.0)
    assert result.stdout_text() == "�a"
    assert result.stderr_text() == "�b"


def test_parent_environment_does_not_leak(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_DIR", "/elsewhere")
    monkeypatch.setenv("PYTHONPATH", "/injected")
    result = run(sh('echo "${GIT_DIR-unset} ${PYTHONPATH-unset} $LC_ALL"', tmp_path))
    assert result.stdout_text() == "unset unset C.UTF-8\n"


def test_commands_get_an_empty_environment_by_default(tmp_path: Path) -> None:
    command = Command(("/bin/sh", "-c", 'echo "${HOME-unset}"'), cwd=tmp_path, timeout=5)
    assert run(command).stdout_text() == "unset\n"


def test_executables_resolve_only_from_the_command_path(tmp_path: Path) -> None:
    command = Command(("sh", "-c", "true"), cwd=tmp_path, timeout=5, env={"PATH": str(tmp_path)})
    with pytest.raises(ExecutableNotFoundError, match="not found on PATH"):
        run(command)


def test_relative_executables_resolve_against_cwd(tmp_path: Path) -> None:
    script = tmp_path / "tool.sh"
    script.write_text("#!/bin/sh\necho tool\n")
    script.chmod(0o755)
    command = Command(("./tool.sh",), cwd=tmp_path, timeout=5, env=sanitized())
    assert run(command).stdout_text() == "tool\n"


def test_non_executable_paths_are_rejected(tmp_path: Path) -> None:
    script = tmp_path / "tool.sh"
    script.write_text("echo tool\n")
    command = Command((str(script),), cwd=tmp_path, timeout=5, env=sanitized())
    with pytest.raises(ExecutableNotFoundError, match="executable not found"):
        run(command)


@pytest.mark.parametrize(
    ("argv", "cwd", "timeout", "message"),
    [
        ((), None, 5.0, "empty argument vector"),
        (("echo", "a\0b"), None, 5.0, "NUL byte"),
        (("echo",), Path("relative"), 5.0, "not an absolute directory"),
        (("echo",), Path("/nonexistent/dir"), 5.0, "not an absolute directory"),
        (("echo",), None, 0.0, "timeout must be positive"),
    ],
)
def test_invalid_commands_are_rejected(
    tmp_path: Path, argv: tuple[str, ...], cwd: Path | None, timeout: float, message: str
) -> None:
    command = Command(argv, cwd=cwd or tmp_path, timeout=timeout, env=sanitized())
    with pytest.raises(InvalidCommandError, match=message):
        run(command)


def test_timeout_kills_the_whole_process_group(tmp_path: Path) -> None:
    command = sh("sleep 600 & echo $! > child.pid; wait", tmp_path, timeout=2)
    with pytest.raises(ProcessTimeoutError, match=r"timed out after 2s"):
        run(command)
    assert wait_gone(int((tmp_path / "child.pid").read_text()))


def test_timeout_escalates_to_sigkill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "TERMINATE_GRACE_SECONDS", 0.2)
    script = "trap '' TERM; echo $$ > leader.pid; while :; do sleep 0.05; done"
    with pytest.raises(ProcessTimeoutError):
        run(sh(script, tmp_path, timeout=2))
    assert wait_gone(int((tmp_path / "leader.pid").read_text()))


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
        run(sh("sleep 30", tmp_path))
    assert len(terminated) == 1
    assert terminated[0].returncode is not None


def test_terminate_tolerates_an_already_reaped_group(tmp_path: Path) -> None:
    process = subprocess.Popen(["/bin/true"], cwd=tmp_path, start_new_session=True)
    process.wait()
    runner.terminate(process)
    assert process.returncode == 0


def test_reap_kills_what_outlives_a_normal_exit(tmp_path: Path) -> None:
    command = dataclasses.replace(sh("sleep 600 & echo $!", tmp_path), reap=True)
    assert wait_gone(int(run(command).stdout_text()))


def test_without_reap_a_background_child_survives(tmp_path: Path) -> None:
    child = int(run(sh("sleep 30 & echo $!", tmp_path)).stdout_text())
    try:
        assert Path(f"/proc/{child}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    finally:
        os.kill(child, signal.SIGKILL)


def test_wait_gone_accepts_a_process_reaped_between_opening_and_reading_its_stat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    child = subprocess.Popen(["/bin/sleep", "600"], cwd=tmp_path)
    stat = Path(f"/proc/{child.pid}/stat")
    original = Path.read_text
    observed: list[int | None] = []
    try:
        with stat.open() as stream:
            child.kill()
            child.wait(timeout=5)

            def read(path: Path) -> str:
                if path != stat:
                    return original(path)
                try:
                    return stream.read()
                except ProcessLookupError as error:
                    observed.append(error.errno)
                    raise

            monkeypatch.setattr(Path, "read_text", read)
            assert wait_gone(child.pid)
            assert observed == [errno.ESRCH]
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)


def test_wait_gone_does_not_hide_stat_permission_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def denied(path: Path) -> str:
        raise PermissionError(errno.EACCES, "permission denied", str(path))

    monkeypatch.setattr(Path, "read_text", denied)
    with pytest.raises(PermissionError):
        wait_gone(os.getpid())
