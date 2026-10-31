import errno
from collections.abc import Iterator

import pytest

from quality.adapters.native import ToolFailedError
from quality.adapters.typescript import ownership
from quality.adapters.typescript.ownership import Child

CHILD = Child(10, 1, "200")


def one_child(_pid: int) -> tuple[Child, ...]:
    return (CHILD,)


def test_reaping_collects_all_exited_children_without_waiting_for_live_ones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replies = iter([(10, 9), (0, 0)])

    def wait(_pid: int, _flags: int) -> tuple[int, int]:
        return next(replies)

    monkeypatch.setattr(ownership.os, "waitpid", wait)
    ownership.reap()


def test_reaping_finishes_when_no_children_remain(monkeypatch: pytest.MonkeyPatch) -> None:
    def absent(_pid: int, _flags: int) -> tuple[int, int]:
        raise ChildProcessError

    monkeypatch.setattr(ownership.os, "waitpid", absent)
    ownership.reap()


def test_cleanup_rechecks_adopted_children_and_reaps_after_killing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adopted = Child(20, 1, "300")
    snapshots = iter([(CHILD,), (adopted,), (adopted,), ()])
    killed: list[Child] = []
    waited: list[float] = []
    reaped: list[bool] = []

    def snapshot(_pid: int) -> tuple[Child, ...]:
        return next(snapshots)

    monkeypatch.setattr(ownership, "children", snapshot)
    monkeypatch.setattr(ownership, "kill_child", killed.append)
    monkeypatch.setattr(ownership, "reap", lambda: reaped.append(True))
    monkeypatch.setattr(ownership.time, "sleep", waited.append)
    ownership.cleanup()
    assert killed == [CHILD, adopted]
    assert reaped == [True, True]
    assert waited == [ownership.POLL]


def test_handler_cleanup_adopts_multiple_generations_without_freeing_zombie_pids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child = CHILD
    adopted = Child(20, 1, "300")
    grandchild = Child(30, 1, "400")
    zombies = tuple(Child(c.pid, c.parent, c.started, "Z") for c in (child, adopted, grandchild))
    snapshots = iter(
        [
            (child,),
            (zombies[0], adopted),
            (zombies[0], adopted),
            (*zombies[:2], grandchild),
            (*zombies[:2], grandchild),
            zombies,
        ]
    )
    killed: list[Child] = []

    def snapshot(_owner: int) -> tuple[Child, ...]:
        return next(snapshots)

    def forbidden() -> None:
        pytest.fail("the signal handler freed a PID still owned by Popen")

    monkeypatch.setattr(ownership, "children", snapshot)
    monkeypatch.setattr(ownership, "kill_child", killed.append)
    monkeypatch.setattr(ownership, "reap", forbidden)
    ownership.cleanup(reap_children=False)
    assert killed == [child, adopted, grandchild]


def test_finalizer_reaps_zombies_reserved_during_the_signal_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshots = iter([(Child(10, 1, "200", "Z"),), ()])
    killed: list[Child] = []
    reaped: list[bool] = []

    def snapshot(_owner: int) -> tuple[Child, ...]:
        return next(snapshots)

    monkeypatch.setattr(ownership, "children", snapshot)
    monkeypatch.setattr(ownership, "kill_child", killed.append)
    monkeypatch.setattr(ownership, "reap", lambda: reaped.append(True))
    ownership.cleanup()
    assert killed == []
    assert reaped == [True]


def test_cleanup_has_a_hard_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    times = iter([0.0, ownership.CLEANUP_TIMEOUT])
    monkeypatch.setattr(ownership.time, "monotonic", lambda: next(times))

    def ignore(_child: Child) -> None:
        pass

    monkeypatch.setattr(ownership, "children", one_child)
    monkeypatch.setattr(ownership, "kill_child", ignore)
    monkeypatch.setattr(ownership, "reap", lambda: None)
    with pytest.raises(ToolFailedError, match="owned descendant processes did not stop: 10"):
        ownership.cleanup()


def test_cleanup_reports_an_actual_signalling_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def denied(_child: Child) -> None:
        raise PermissionError(errno.EPERM, "denied")

    monkeypatch.setattr(ownership, "children", one_child)
    monkeypatch.setattr(ownership, "kill_child", denied)
    with pytest.raises(ToolFailedError, match="cannot stop owned descendant 10"):
        ownership.cleanup()


class Kernel:
    def __init__(self, results: Iterator[int]) -> None:
        self.results = results
        self.options: list[int] = []

    def prctl(self, option: int, _value: object, *_args: int) -> int:
        self.options.append(option)
        return next(self.results)


@pytest.mark.parametrize("results", [[0, 0], [1], [0, 1]])
def test_subreaper_setup_checks_each_kernel_result(
    monkeypatch: pytest.MonkeyPatch, results: list[int]
) -> None:
    kernel = Kernel(iter(results))

    def library(_name: str | None, **_options: object) -> Kernel:
        return kernel

    monkeypatch.setattr(ownership.sys, "platform", "linux")
    monkeypatch.setattr(ownership.ctypes, "CDLL", library)
    monkeypatch.setattr(ownership.ctypes, "get_errno", lambda: errno.EPERM)
    if all(result == 0 for result in results):
        assert ownership.subreaper(1) == 0
    else:
        with pytest.raises(OSError, match="child-subreaper setting"):
            ownership.subreaper(1)
    assert kernel.options == [ownership.GET_SUBREAPER, ownership.SET_SUBREAPER][: len(results)]


def test_an_unsupported_platform_is_an_explicit_environment_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ownership.sys, "platform", "unsupported")
    with (
        pytest.raises(ToolFailedError, match="requires Linux with glibc"),
        ownership.owned_processes(),
    ):
        pytest.fail("unsupported platform established ownership")


@pytest.mark.parametrize("failed", [False, True])
def test_pidfd_capability_probe_closes_its_handle_even_when_signalling_fails(
    monkeypatch: pytest.MonkeyPatch, failed: bool
) -> None:
    observed: list[tuple[int, int]] = []
    closed: list[int] = []

    def opened(pid: int) -> int:
        assert pid == ownership.os.getpid()
        return 42

    def send(descriptor: int, signum: int) -> None:
        observed.append((descriptor, signum))
        if failed:
            raise OSError(errno.ENOSYS, "pidfd unavailable")

    monkeypatch.setattr(ownership.os, "pidfd_open", opened)
    monkeypatch.setattr(ownership.signal, "pidfd_send_signal", send)
    monkeypatch.setattr(ownership.os, "close", closed.append)
    if failed:
        with pytest.raises(OSError):
            ownership.check_pidfds()
    else:
        ownership.check_pidfds()
    assert observed == [(42, 0)]
    assert closed == [42]


def test_missing_pidfds_block_before_subreaping_or_launching_children(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable() -> None:
        raise OSError(errno.ENOSYS, "pidfd unavailable")

    def forbidden(_enabled: int) -> int:
        pytest.fail("subreaping started before the capability probe passed")

    monkeypatch.setattr(ownership, "check_pidfds", unavailable)
    monkeypatch.setattr(ownership, "subreaper", forbidden)
    with (
        pytest.raises(ToolFailedError, match="Linux pidfd support is required"),
        ownership.owned_processes(),
    ):
        pytest.fail("children could be launched without pidfd support")
