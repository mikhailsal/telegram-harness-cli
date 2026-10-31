import errno
import signal
from pathlib import Path

import pytest

from quality.adapters.typescript import ownership
from quality.adapters.typescript.ownership import Child

CHILD = Child(10, 1, "200")


def stat(root: Path, child: Child) -> None:
    directory = root / str(child.pid)
    directory.mkdir()
    fields = ["S", str(child.parent), *(["0"] * 17), child.started]
    (directory / "stat").write_text(f"{child.pid} (name with spaces) " + " ".join(fields))


def test_stale_numeric_ancestor_links_never_assign_ownership_to_other_processes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for child in (CHILD, Child(20, 10, "300"), Child(30, 20, "400"), Child(40, 2, "500")):
        stat(tmp_path, child)
    for name, data in (("50", "50 (bad) S invalid"), ("60", "60 (short) S")):
        (tmp_path / name).mkdir()
        (tmp_path / name / "stat").write_text(data)
    (tmp_path / "70").mkdir()
    monkeypatch.setattr(ownership, "PROC", tmp_path)
    # A stale observation of PID10 must never classify PID20 or30 as owned.
    assert {child.pid for child in ownership.children(1)} == {10}


@pytest.mark.parametrize("started", ["200", "201", None])
def test_identity_is_checked_before_signalling_a_pid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, started: str | None
) -> None:
    if started is not None:
        stat(tmp_path, Child(10, 1, started))
    monkeypatch.setattr(ownership, "PROC", tmp_path)
    assert ownership.same_process(CHILD) is (started == "200")


@pytest.mark.parametrize("same", [False, True])
@pytest.mark.parametrize("vanished", [False, True])
def test_pidfd_signals_only_the_observed_process_and_always_closes(
    monkeypatch: pytest.MonkeyPatch, same: bool, vanished: bool
) -> None:
    sent: list[tuple[int, signal.Signals]] = []
    closed: list[int] = []

    def send(descriptor: int, signum: signal.Signals) -> None:
        sent.append((descriptor, signum))
        if vanished:
            raise ProcessLookupError

    def opened(_pid: int) -> int:
        return 42

    def identity(_child: Child) -> bool:
        return same

    monkeypatch.setattr(ownership.os, "pidfd_open", opened)
    monkeypatch.setattr(ownership, "same_process", identity)
    monkeypatch.setattr(ownership.signal, "pidfd_send_signal", send)
    monkeypatch.setattr(ownership.os, "close", closed.append)
    ownership.kill_child(CHILD)
    assert sent == ([(42, signal.SIGKILL)] if same else [])
    assert closed == [42]


def test_a_process_gone_before_pidfd_open_needs_no_signal(monkeypatch: pytest.MonkeyPatch) -> None:
    def vanished(_pid: int) -> int:
        raise ProcessLookupError

    monkeypatch.setattr(ownership.os, "pidfd_open", vanished)
    ownership.kill_child(CHILD)


def test_unavailable_pidfds_never_fall_back_to_a_numeric_pid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[tuple[int, signal.Signals]] = []

    def unavailable(_pid: int) -> int:
        raise OSError(errno.ENOSYS, "pidfd unavailable")

    def kill(pid: int, signum: signal.Signals) -> None:
        sent.append((pid, signum))

    monkeypatch.setattr(ownership.os, "pidfd_open", unavailable)
    monkeypatch.setattr(ownership.os, "kill", kill)
    with pytest.raises(OSError) as raised:
        ownership.kill_child(CHILD)
    assert raised.value.errno == errno.ENOSYS
    assert sent == []


def test_an_unexpected_pidfd_error_is_not_hidden(monkeypatch: pytest.MonkeyPatch) -> None:
    def denied(_pid: int) -> int:
        raise PermissionError(errno.EPERM, "denied")

    monkeypatch.setattr(ownership.os, "pidfd_open", denied)
    with pytest.raises(PermissionError):
        ownership.kill_child(CHILD)
