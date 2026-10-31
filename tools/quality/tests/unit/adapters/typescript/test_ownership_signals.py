import signal

import pytest

from quality.adapters.native import ToolFailedError
from quality.adapters.typescript import ownership


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
def test_signals_kill_children_before_unwinding_the_inner_runner(
    monkeypatch: pytest.MonkeyPatch, signum: signal.Signals
) -> None:
    observed: list[object] = []

    def setting(*args: object) -> None:
        observed.append(args)

    monkeypatch.setattr(ownership.signal, "signal", setting)

    def cleanup(*, reap_children: bool) -> None:
        observed.append(("cleanup", reap_children))

    monkeypatch.setattr(ownership, "cleanup", cleanup)
    if signum == signal.SIGINT:
        with pytest.raises(KeyboardInterrupt):
            ownership.interrupt(signum, None)
    else:
        with pytest.raises(SystemExit) as raised:
            ownership.interrupt(signum, None)
        assert raised.value.code == 128 + signum
    assert observed == [
        (signal.SIGTERM, signal.SIG_IGN),
        (signal.SIGINT, signal.SIG_IGN),
        ("cleanup", False),
    ]


@pytest.mark.parametrize("raises", [False, True])
def test_ownership_restores_state_even_when_cleanup_fails(
    monkeypatch: pytest.MonkeyPatch, raises: bool
) -> None:
    flags: list[int] = []
    handlers: list[object] = []

    def subreaper(value: int) -> int:
        flags.append(value)
        return 0

    def cleanup() -> None:
        if raises:
            message = "cannot clean up"
            raise RuntimeError(message)

    def setting(*args: object) -> signal.Handlers:
        handlers.append(args)
        return signal.SIG_DFL

    monkeypatch.setattr(ownership, "subreaper", subreaper)
    monkeypatch.setattr(ownership, "check_pidfds", lambda: None)
    monkeypatch.setattr(ownership, "cleanup", cleanup)
    monkeypatch.setattr(ownership.signal, "signal", setting)
    if raises:
        with pytest.raises(RuntimeError, match="cannot clean up"), ownership.owned_processes():
            pass
    else:
        with ownership.owned_processes():
            pass
    assert flags == [1, 0]
    assert handlers[-2:] == [(signal.SIGTERM, signal.SIG_DFL), (signal.SIGINT, signal.SIG_DFL)]


def test_failed_ownership_setup_names_the_environment_problem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def denied(_value: int) -> int:
        message = "prctl denied"
        raise OSError(message)

    monkeypatch.setattr(ownership, "subreaper", denied)
    monkeypatch.setattr(ownership, "check_pidfds", lambda: None)
    with (
        pytest.raises(ToolFailedError, match="cannot own browser descendant processes"),
        ownership.owned_processes(),
    ):
        pytest.fail("ownership was not established")
