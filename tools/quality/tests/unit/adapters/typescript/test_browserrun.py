"""The isolated browser worker owns both its loopback server and structured reply."""

import http.client
import io
import os
import sys
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from quality.adapters.native import ToolFailedError, failure
from quality.adapters.typescript import browserrun
from quality.adapters.typescript.browserrun import BrowserJob, BrowserReply
from quality.adapters.typescript.bundle import collect
from quality.adapters.typescript.environment import Node
from quality.boundedio import JsonValue
from quality.policy import Suite
from quality.process import Command, Completed, ProcessError
from quality.testing import SuiteRequest
from quality.testkit.nodefakes import browser_component


@pytest.fixture
def job(tmp_path: Path, node: Node) -> BrowserJob:
    snapshot, output = tmp_path / "snapshot", tmp_path / "work/web-e2e"
    (snapshot / "web").mkdir(parents=True)
    output.mkdir(parents=True)
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<p>Owned page</p>\n")
    request = SuiteRequest(
        browser_component(), Suite.E2E, ("e2e",), snapshot, output, 0.0, tmp_path / "work", "tree"
    )
    return BrowserJob(
        node, tmp_path / "playwright", tmp_path / "shell", request, collect(dist, "tree"), "/app/"
    )


def fetch(url: str) -> int:
    address = urlsplit(url)
    connection = http.client.HTTPConnection(address.hostname or "", address.port, timeout=5)
    try:
        connection.request("GET", address.path)
        response = connection.getresponse()
        response.read()
        return response.status
    finally:
        connection.close()


def test_job_and_observations_roundtrip_without_losing_paths_or_candidate_identity(
    job: BrowserJob,
) -> None:
    assert browserrun.JOB.validate_json(browserrun.JOB.dump_json(job)) == job
    reply = BrowserReply(
        1,
        job.request.output / "results.json",
        "http://127.0.0.1:4000",
        ("/app/logo.svg",),
        3,
        tokens=("first-document", "second-document", "third-document"),
    )
    assert browserrun.REPLY.validate_json(browserrun.REPLY.dump_json(reply)) == reply


def test_execution_observes_owned_documents_and_missing_files_and_closes_the_server(
    job: BrowserJob, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: list[str] = []

    def playwright(
        node: Node, script: Path, request: SuiteRequest, values: dict[str, JsonValue]
    ) -> tuple[int, Path]:
        assert (node, script, request) == (job.node, job.playwright, job.request)
        assert values["executable"] == str(job.browser)
        url = str(values["baseURL"])
        observed.append(url)
        assert fetch(url) == 200
        assert fetch(url + "missing.svg") == 404
        assert fetch(url + "route") == 200
        return 1, request.output / "results.json"

    monkeypatch.setattr(browserrun, "run_playwright", playwright)
    reply = browserrun.execute(job)
    assert reply == BrowserReply(
        1,
        job.request.output / "results.json",
        observed[0].removesuffix("/app/"),
        ("/app/missing.svg",),
        2,
        tokens=reply.tokens,
    )
    assert len(reply.tokens) == 2
    assert len(set(reply.tokens)) == 2
    with pytest.raises(ConnectionRefusedError):
        fetch(observed[0])


def test_a_playwright_tool_failure_also_closes_the_owned_server(
    job: BrowserJob, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: list[str] = []
    error = failure("playwright", "launch failed")

    def playwright(
        node: Node, script: Path, request: SuiteRequest, values: dict[str, JsonValue]
    ) -> tuple[int, Path]:
        observed.append(str(values["baseURL"]))
        assert fetch(observed[0]) == 200
        raise error

    monkeypatch.setattr(browserrun, "run_playwright", playwright)
    with pytest.raises(ToolFailedError, match="playwright: launch failed"):
        browserrun.execute(job)
    with pytest.raises(ConnectionRefusedError):
        fetch(observed[0])


@pytest.mark.parametrize("failed", [False, True])
def test_main_returns_a_structured_reply_including_tool_diagnostics(
    job: BrowserJob, monkeypatch: pytest.MonkeyPatch, failed: bool
) -> None:
    diagnostic = failure("playwright", "failed to launch").diagnostic
    expected = BrowserReply(error=diagnostic) if failed else BrowserReply(0)
    observed: list[str] = []

    @contextmanager
    def owned() -> Generator[None]:
        observed.append("handlers installed")
        try:
            yield
        finally:
            observed.append("finalized")

    def watch(parent: int) -> None:
        assert parent == 123
        observed.append("parent watched")

    def execute(received: BrowserJob) -> BrowserReply:
        assert received == job
        observed.append("execute")
        if failed:
            raise ToolFailedError(diagnostic)
        return expected

    stream = io.BytesIO()
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(browserrun.JOB.dump_json(job))))
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(stream))
    monkeypatch.setattr(browserrun, "execute", execute)
    monkeypatch.setattr(browserrun, "owned_processes", owned)
    monkeypatch.setattr(browserrun, "watch_parent", watch)
    browserrun.main(123)
    assert browserrun.REPLY.validate_json(stream.getvalue()) == expected
    assert observed == ["handlers installed", "parent watched", "execute", "finalized"]


def test_a_parent_watch_failure_finalizes_without_starting_the_server_or_browser(
    job: BrowserJob, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: list[str] = []
    diagnostic = failure("browser worker", "cannot watch parent").diagnostic

    @contextmanager
    def owned() -> Generator[None]:
        try:
            yield
        finally:
            observed.append("finalized")

    def watch(_parent: int) -> None:
        raise ToolFailedError(diagnostic)

    def forbidden(_job: BrowserJob) -> BrowserReply:
        pytest.fail("server or browser started without parent-death ownership")

    stream = io.BytesIO()
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(browserrun.JOB.dump_json(job))))
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(stream))
    monkeypatch.setattr(browserrun, "owned_processes", owned)
    monkeypatch.setattr(browserrun, "watch_parent", watch)
    monkeypatch.setattr(browserrun, "execute", forbidden)
    browserrun.main(123)
    assert browserrun.REPLY.validate_json(stream.getvalue()) == BrowserReply(error=diagnostic)
    assert observed == ["finalized"]


@pytest.mark.parametrize("unisolated", [None, "network namespaces unavailable"])
def test_owned_worker_uses_isolation_and_trusted_core_with_serialized_input(
    job: BrowserJob, monkeypatch: pytest.MonkeyPatch, unisolated: str | None
) -> None:
    commands: list[Command] = []
    reply = BrowserReply(0, job.request.output / "results.json", "http://127.0.0.1:4000")

    def isolate(request: SuiteRequest, argv: tuple[str, ...]) -> tuple[tuple[str, ...], str | None]:
        assert request == job.request
        assert argv[:4] == (sys.executable, "-I", "-c", browserrun.BOOT)
        assert argv[4] == str(Path(browserrun.__file__).resolve().parents[3])
        assert argv[5:] == (str(os.getpid()),)
        return ("loopback-only", *argv), unisolated

    def run(command: Command) -> Completed:
        commands.append(command)
        return Completed(command.argv, 0, browserrun.REPLY.dump_json(reply), b"", 0.0)

    monkeypatch.setenv("PYTHONPATH", "/untrusted-candidate")
    monkeypatch.setattr(browserrun, "suite_command", isolate)
    monkeypatch.setattr(browserrun, "run", run)
    assert browserrun.run_owned(job) == (reply, unisolated)
    (command,) = commands
    assert command.argv[0] == "loopback-only"
    assert command.cwd == job.request.directory
    assert command.timeout == browserrun.SUITE_TIMEOUT + 60
    assert command.reap
    assert command.stdin is not None
    assert browserrun.JOB.validate_json(command.stdin) == job
    assert "PYTHONPATH" not in command.env


@pytest.mark.parametrize("stderr", [b"worker crashed", b""])
def test_a_worker_process_failure_reports_stderr_or_stdout(
    job: BrowserJob, monkeypatch: pytest.MonkeyPatch, stderr: bytes
) -> None:
    def run(command: Command) -> Completed:
        return Completed(command.argv, 2, b"worker stdout", stderr, 0.0)

    monkeypatch.setattr(browserrun, "run", run)
    message = (stderr or b"worker stdout").decode()
    with pytest.raises(ToolFailedError, match=f"browser worker: {message}"):
        browserrun.run_owned(job)


def test_a_worker_that_cannot_start_is_a_structured_tool_failure(
    job: BrowserJob, monkeypatch: pytest.MonkeyPatch
) -> None:
    def run(command: Command) -> Completed:
        raise ProcessError(command.argv, "cannot start worker")

    monkeypatch.setattr(browserrun, "run", run)
    with pytest.raises(ToolFailedError, match="browser worker: cannot start worker") as caught:
        browserrun.run_owned(job)
    assert caught.value.diagnostic.rule == "tools.failed"


def test_a_worker_tool_diagnostic_is_preserved_by_the_parent(
    job: BrowserJob, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = failure("playwright", "browser unavailable").diagnostic
    diagnostic = replace(original, data=dict(original.data))

    def run(command: Command) -> Completed:
        reply = BrowserReply(error=diagnostic)
        return Completed(command.argv, 0, browserrun.REPLY.dump_json(reply), b"", 0.0)

    monkeypatch.setattr(browserrun, "run", run)
    with pytest.raises(ToolFailedError) as caught:
        browserrun.run_owned(job)
    assert caught.value.diagnostic == diagnostic
