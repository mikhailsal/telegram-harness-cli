"""Safe subprocess execution: argument arrays, explicit cwd, bounded output."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO

DEFAULT_OUTPUT_LIMIT = 16 * 1024 * 1024
TERMINATE_GRACE_SECONDS = 5.0


class ProcessError(Exception):
    """A command could not be run to completion."""

    def __init__(self, argv: tuple[str, ...], message: str) -> None:
        super().__init__(f"{message}: {' '.join(argv)}")
        self.argv = argv


class InvalidCommandError(ProcessError):
    """The command specification itself is invalid."""


class ExecutableNotFoundError(ProcessError):
    """The executable is not available on the provided PATH."""


class ProcessTimeoutError(ProcessError):
    """The command exceeded its timeout and was terminated."""


def _empty_env() -> Mapping[str, str]:
    return {}


@dataclass(frozen=True, slots=True)
class Command:
    """A fully specified command; nothing is inherited implicitly.

    The command runs in its own session, whose process group is killed on a
    timeout. With `reap`, whatever is left of that group is also killed after a
    normal exit, for tools such as `dart test` whose helper processes may
    outlive the command.
    """

    argv: tuple[str, ...]
    cwd: Path
    timeout: float
    env: Mapping[str, str] = field(default_factory=_empty_env)
    stdin: bytes | None = None
    output_limit: int = DEFAULT_OUTPUT_LIMIT
    reap: bool = False


@dataclass(frozen=True, slots=True)
class Completed:
    """The result of a command that ran to completion."""

    argv: tuple[str, ...]
    returncode: int
    stdout: bytes
    stderr: bytes
    duration: float
    stdout_truncated: bool = False
    stderr_truncated: bool = False

    @property
    def ok(self) -> bool:
        """Whether the command exited with status zero."""
        return self.returncode == 0

    def stdout_text(self) -> str:
        """Decode stdout as UTF-8, replacing invalid bytes."""
        return self.stdout.decode("utf-8", errors="replace")

    def stderr_text(self) -> str:
        """Decode stderr as UTF-8, replacing invalid bytes."""
        return self.stderr.decode("utf-8", errors="replace")


def _validate(command: Command) -> None:
    if not command.argv:
        raise InvalidCommandError(command.argv, "empty argument vector")
    if any("\0" in arg for arg in command.argv):
        raise InvalidCommandError(command.argv, "NUL byte in argument")
    if not command.cwd.is_absolute() or not command.cwd.is_dir():
        raise InvalidCommandError(
            command.argv, f"working directory is not an absolute directory ({command.cwd})"
        )
    if command.timeout <= 0:
        raise InvalidCommandError(command.argv, "timeout must be positive")


def resolve_executable(command: Command) -> str:
    """Resolve argv[0] against the command's own PATH, never the parent's."""
    name = command.argv[0]
    if "/" in name:
        path = Path(name)
        if not path.is_absolute():
            path = command.cwd / path
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
        raise ExecutableNotFoundError(command.argv, "executable not found")
    found = shutil.which(name, path=command.env.get("PATH", ""))
    if found is None:
        raise ExecutableNotFoundError(command.argv, "executable not found on PATH")
    return found


def _read_bounded(stream: IO[bytes], limit: int) -> tuple[bytes, bool]:
    stream.seek(0)
    data = stream.read(limit + 1)
    return data[:limit], len(data) > limit


def _signal_group(process: subprocess.Popen[bytes], sig: signal.Signals) -> None:
    try:
        os.killpg(process.pid, sig)
    except ProcessLookupError:
        return


def terminate(process: subprocess.Popen[bytes]) -> None:
    """Terminate the whole process group, escalating to SIGKILL."""
    _signal_group(process, signal.SIGTERM)
    try:
        process.wait(timeout=TERMINATE_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        _signal_group(process, signal.SIGKILL)
        process.wait()
    # Children may outlive the leader; make sure the group is gone.
    _signal_group(process, signal.SIGKILL)


def run(command: Command) -> Completed:
    """Run a command to completion or raise a `ProcessError`."""
    _validate(command)
    executable = resolve_executable(command)
    started = time.monotonic()
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        process = subprocess.Popen(
            [executable, *command.argv[1:]],
            cwd=command.cwd,
            env=dict(command.env),
            stdin=subprocess.PIPE if command.stdin is not None else subprocess.DEVNULL,
            stdout=out,
            stderr=err,
            start_new_session=True,
            close_fds=True,
        )
        try:
            process.communicate(input=command.stdin, timeout=command.timeout)
        except subprocess.TimeoutExpired:
            terminate(process)
            raise ProcessTimeoutError(
                command.argv, f"timed out after {command.timeout:g}s"
            ) from None
        except BaseException:
            terminate(process)
            raise
        if command.reap:
            _signal_group(process, signal.SIGKILL)
        stdout, stdout_truncated = _read_bounded(out, command.output_limit)
        stderr, stderr_truncated = _read_bounded(err, command.output_limit)
    return Completed(
        argv=command.argv,
        returncode=process.returncode,
        stdout=stdout,
        stderr=stderr,
        duration=time.monotonic() - started,
        stdout_truncated=stdout_truncated,
        stderr_truncated=stderr_truncated,
    )


def run_attached(command: Command) -> int:
    """Run a command that writes straight to this process's stdout and stderr.

    Only the output is shared; the environment, directory, and timeout are the
    command's own, as with `run`. Returns the exit status.
    """
    _validate(command)
    executable = resolve_executable(command)
    sys.stdout.flush()
    sys.stderr.flush()
    process = subprocess.Popen(
        [executable, *command.argv[1:]],
        cwd=command.cwd,
        env=dict(command.env),
        stdin=subprocess.PIPE if command.stdin is not None else subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )
    try:
        process.communicate(input=command.stdin, timeout=command.timeout)
    except subprocess.TimeoutExpired:
        terminate(process)
        raise ProcessTimeoutError(command.argv, f"timed out after {command.timeout:g}s") from None
    except BaseException:
        terminate(process)
        raise
    return process.returncode
