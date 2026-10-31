"""Own and reap detached descendants of a dedicated Linux browser worker.

Node and Chromium start independent sessions, so killing the worker's process group is
insufficient. A subreaper adopts orphaned descendants. Both interruption handlers and
the finalizer kill those descendants directly before the outer runner's grace expires.
Linux with glibc is already a toolchain prerequisite (decision 0002).
"""

from __future__ import annotations

import ctypes
import os
import signal
import sys
import time
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import FrameType

from quality.adapters.native import fail

PROC = Path("/proc")
SET_PARENT_DEATH_SIGNAL = 1
GET_SUBREAPER = 37
SET_SUBREAPER = 36
CLEANUP_TIMEOUT = 1.0
POLL = 0.01
TOOL = "browser worker"


@dataclass(frozen=True, slots=True)
class Child:
    """The process identity needed to avoid signalling a reused PID."""

    pid: int
    parent: int
    started: str
    state: str = "S"


def _read(path: Path) -> Child:
    fields = (path / "stat").read_text().rpartition(")")[2].split()
    return Child(int(path.name), int(fields[1]), fields[19], fields[0])


def children(owner: int) -> tuple[Child, ...]:
    """Find only direct children, whose PIDs this worker has not yet reaped.

    Never infer ownership through numeric ancestor links: that ancestor could have
    exited and its PID could identify an unrelated process by the next procfs read.
    """
    found: list[Child] = []
    for path in PROC.glob("[0-9]*"):
        try:
            child = _read(path)
        except OSError, ValueError, IndexError:
            continue
        if child.parent == owner:
            found.append(child)
    return tuple(found)


def same_process(child: Child) -> bool:
    """Whether the observed PID still identifies the process whose lifetime we own."""
    try:
        return _read(PROC / str(child.pid)).started == child.started
    except OSError, ValueError, IndexError:
        return False


def kill_child(child: Child) -> None:
    """Kill an observed descendant without signalling a replacement with the same PID."""
    try:
        descriptor = os.pidfd_open(child.pid)
    except ProcessLookupError:
        return
    try:
        if same_process(child):
            signal.pidfd_send_signal(descriptor, signal.SIGKILL)
    except ProcessLookupError:
        pass
    finally:
        os.close(descriptor)


def reap() -> None:
    """Reap exited direct or adopted children without waiting for live children."""
    while True:
        try:
            pid, _status = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        if pid == 0:
            return


def _kill_live_children(owner: int) -> None:
    for child in children(owner):
        if child.state == "Z":
            continue
        try:
            kill_child(child)
        except OSError as error:
            fail(TOOL, f"cannot stop owned descendant {child.pid}: {error}")


def cleanup(*, reap_children: bool = True) -> None:
    """Kill adopted children in order and optionally reap their reserved PIDs.

    During a signal handler, leave zombies for the suspended Popen owner to reap
    when it resumes. The outer finalizer reaps remaining adopted zombies afterward.
    """
    deadline = time.monotonic() + CLEANUP_TIMEOUT
    while True:
        _kill_live_children(os.getpid())
        if reap_children:
            reap()
        remaining = children(os.getpid())
        if not reap_children:
            remaining = tuple(child for child in remaining if child.state != "Z")
        if not remaining:
            return
        if time.monotonic() >= deadline:
            names = ", ".join(str(child.pid) for child in remaining)
            fail(TOOL, f"owned descendant processes did not stop: {names}")
        time.sleep(POLL)


def subreaper(enabled: int) -> int:
    """Set Linux child adoption and return the previous setting."""
    library = ctypes.CDLL(None, use_errno=True)
    previous = ctypes.c_int()
    if library.prctl(GET_SUBREAPER, ctypes.byref(previous), 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "cannot read child-subreaper setting")
    if library.prctl(SET_SUBREAPER, enabled, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "cannot set child-subreaper setting")
    return previous.value


def check_pidfds() -> None:
    """Require race-free process handles before the worker launches any children."""
    descriptor = os.pidfd_open(os.getpid())
    try:
        signal.pidfd_send_signal(descriptor, 0)
    finally:
        os.close(descriptor)


def interrupt(signum: int, _frame: FrameType | None) -> None:
    """Stop descendants before an interruption unwinds the nested subprocess runner."""
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    cleanup(reap_children=False)
    if signum == signal.SIGINT:
        raise KeyboardInterrupt
    raise SystemExit(128 + signum)


def watch_parent(expected_parent: int) -> None:
    """Arm cleanup on parent death, checking the startup race before launching children.

    The expected PID comes from the trusted launching process, before any namespace
    wrapper. A parent that died before prctl will not generate a retroactive signal.
    """
    try:
        library = ctypes.CDLL(None, use_errno=True)
    except OSError as error:
        fail(TOOL, f"cannot watch browser worker parent: {error}")
    if library.prctl(SET_PARENT_DEATH_SIGNAL, signal.SIGTERM, 0, 0, 0) != 0:
        error = OSError(ctypes.get_errno(), "cannot set parent-death signal")
        fail(TOOL, f"cannot watch browser worker parent: {error}")
    if os.getppid() != expected_parent:
        interrupt(signal.SIGTERM, None)


@contextmanager
def owned_processes() -> Generator[None]:
    """Own all children of this dedicated worker until every exit path has reaped them."""
    if sys.platform != "linux":
        fail(TOOL, "owning browser descendants requires Linux with glibc (decision 0002)")
    try:
        check_pidfds()
        previous = subreaper(1)
    except OSError as error:
        fail(
            TOOL,
            f"cannot own browser descendant processes; Linux pidfd support is required: {error}",
        )
    term = signal.signal(signal.SIGTERM, interrupt)
    previous_interrupt = signal.signal(signal.SIGINT, interrupt)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            cleanup()
        finally:
            signal.signal(signal.SIGTERM, term)
            signal.signal(signal.SIGINT, previous_interrupt)
            subreaper(previous)
