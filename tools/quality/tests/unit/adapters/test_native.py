from pathlib import Path

import pytest

from quality.adapters import native
from quality.adapters.native import (
    EXCERPT,
    ToolFailedError,
    fail,
    failure,
    run_tool,
    suite_command,
)
from quality.diagnostics import Kind, Severity
from quality.policy import Component
from quality.process import Command, isolation, sanitized
from quality.testing import Suite, SuiteRequest
from quality.testkit.nodefakes import browser_component
from quality.testkit.policies import ComponentSpec, build_policy


def sh(script: str, cwd: Path, *, timeout: float = 10) -> Command:
    return Command(("sh", "-c", script), cwd=cwd, timeout=timeout, env=sanitized())


def test_accepted_statuses_return_the_result(tmp_path: Path) -> None:
    completed = run_tool(sh("echo found; exit 4", tmp_path), name="demo", accepted={0, 4})
    assert (completed.returncode, completed.stdout_text()) == (4, "found\n")


@pytest.mark.parametrize(
    ("script", "message"),
    [
        ("echo out; echo err >&2; exit 3", "demo: exit status 3: err"),
        ("echo only stdout; exit 2", "demo: exit status 2: only stdout"),
        ("exit 1", "demo: exit status 1:"),
    ],
)
def test_other_statuses_fail_with_the_output(tmp_path: Path, script: str, message: str) -> None:
    with pytest.raises(ToolFailedError) as raised:
        run_tool(sh(script, tmp_path), name="demo", accepted={0})
    diagnostic = raised.value.diagnostic
    assert (diagnostic.rule, diagnostic.message) == ("tools.failed", message)
    assert (diagnostic.kind, diagnostic.severity) == (Kind.ENVIRONMENT, Severity.ERROR)
    assert str(raised.value) == message


def test_missing_executables_fail(tmp_path: Path) -> None:
    command = Command((str(tmp_path / "absent"),), cwd=tmp_path, timeout=10, env=sanitized())
    with pytest.raises(ToolFailedError) as raised:
        run_tool(command, name="demo", accepted={0})
    assert raised.value.diagnostic.message == f"demo: executable not found: {tmp_path / 'absent'}"


def test_timeouts_fail(tmp_path: Path) -> None:
    with pytest.raises(ToolFailedError) as raised:
        run_tool(sh("sleep 5", tmp_path, timeout=0.2), name="demo", accepted={0})
    assert raised.value.diagnostic.message.startswith("demo: ")
    assert "sleep 5" in raised.value.diagnostic.message


def test_long_output_is_truncated() -> None:
    error = failure("demo", "  " + "x" * (EXCERPT + 10) + "\n")
    assert error.diagnostic.message == "demo: " + "x" * EXCERPT + " [truncated]"
    assert failure("demo", "y" * EXCERPT).diagnostic.message == "demo: " + "y" * EXCERPT


def test_empty_output_names_the_tool() -> None:
    assert failure("demo", " \n").diagnostic.message == "demo failed"


def test_fail_raises_the_failure() -> None:
    with pytest.raises(ToolFailedError, match=r"^demo: broken$"):
        fail("demo", "broken")


def suite_request(suite: Suite) -> SuiteRequest:
    component: Component = build_policy(ComponentSpec()).components[0]
    return SuiteRequest(component, suite, ("tests",), Path("/s"), Path("/o"), 0.0, Path("/w"))


@pytest.mark.parametrize(("reason", "isolated"), [(None, True), ("no namespaces", False)])
def test_only_unit_suites_are_contained(
    monkeypatch: pytest.MonkeyPatch, reason: str | None, isolated: bool
) -> None:
    monkeypatch.setattr(native, "contained", isolation.contained)
    monkeypatch.setattr(isolation, "unavailable", lambda: reason)
    argv = ("pytest", "-q")
    expected = isolation.isolated(argv) if isolated else argv
    assert suite_command(suite_request(Suite.UNIT), argv) == (expected, reason)
    assert suite_command(suite_request(Suite.INTEGRATION), argv) == (argv, None)


def test_owned_browser_suites_share_unit_network_isolation(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, ...]] = []

    def contain(argv: tuple[str, ...]) -> tuple[tuple[str, ...], str | None]:
        calls.append(argv)
        return ("isolated", *argv), None

    monkeypatch.setattr(native, "contained", contain)
    request = SuiteRequest(
        browser_component(), Suite.E2E, ("e2e",), Path("/s"), Path("/o"), 0.0, Path("/w")
    )
    assert suite_command(request, ("browser-worker",)) == (("isolated", "browser-worker"), None)
    assert suite_command(suite_request(Suite.E2E), ("pytest",)) == (("pytest",), None)
    assert calls == [("browser-worker",)]
