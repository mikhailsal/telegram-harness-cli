import errno
import signal

import pytest

from quality.adapters.native import ToolFailedError
from quality.adapters.typescript import ownership


class Kernel:
    def __init__(self, status: int) -> None:
        self.status = status
        self.arguments: list[tuple[int, ...]] = []

    def prctl(self, *arguments: int) -> int:
        self.arguments.append(arguments)
        return self.status


@pytest.mark.parametrize("parent", [123, 456])
def test_parent_death_is_armed_before_the_race_check_without_numeric_signals(
    monkeypatch: pytest.MonkeyPatch, parent: int
) -> None:
    kernel = Kernel(0)

    def library(_name: str | None, **_options: object) -> Kernel:
        return kernel

    def getppid() -> int:
        assert kernel.arguments == [(ownership.SET_PARENT_DEATH_SIGNAL, signal.SIGTERM, 0, 0, 0)]
        return parent

    def forbidden(_pid: int, _signum: int) -> None:
        pytest.fail("parent-death cleanup sent a signal to a numeric PID")

    def cleanup(*, reap_children: bool) -> None:
        assert not reap_children

    def setting(*_arguments: object) -> signal.Handlers:
        return signal.SIG_DFL

    monkeypatch.setattr(ownership.ctypes, "CDLL", library)
    monkeypatch.setattr(ownership.os, "getppid", getppid)
    monkeypatch.setattr(ownership.os, "kill", forbidden)
    monkeypatch.setattr(ownership, "cleanup", cleanup)
    monkeypatch.setattr(ownership.signal, "signal", setting)
    if parent == 123:
        ownership.watch_parent(123)
    else:
        with pytest.raises(SystemExit) as raised:
            ownership.watch_parent(123)
        assert raised.value.code == 143


@pytest.mark.parametrize("load_failed", [False, True])
def test_parent_death_setup_failures_are_explicit_and_do_not_continue(
    monkeypatch: pytest.MonkeyPatch, load_failed: bool
) -> None:
    kernel = Kernel(-1)

    def library(_name: str | None, **_options: object) -> Kernel:
        if load_failed:
            raise OSError(errno.ENOENT, "libc unavailable")
        return kernel

    def forbidden() -> int:
        pytest.fail("worker continued after parent-death setup failed")

    monkeypatch.setattr(ownership.ctypes, "CDLL", library)
    monkeypatch.setattr(ownership.ctypes, "get_errno", lambda: errno.EPERM)
    monkeypatch.setattr(ownership.os, "getppid", forbidden)
    with pytest.raises(ToolFailedError, match="cannot watch browser worker parent"):
        ownership.watch_parent(123)
