"""Start the owned production server and Playwright together in a loopback-only namespace.

Only the gate's installed core is imported by the isolated interpreter. The
JSON job carries the candidate's paths and immutable output manifest; the
reply carries the server's observations independently of Playwright's report.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, replace
from pathlib import Path

from pydantic import TypeAdapter

from quality.adapters.native import ToolFailedError, fail, failure, suite_command
from quality.adapters.typescript.bundle import Output
from quality.adapters.typescript.environment import Node
from quality.adapters.typescript.ownership import owned_processes, watch_parent
from quality.adapters.typescript.playwright import SUITE_TIMEOUT, run_playwright, settings
from quality.adapters.typescript.serve import serve
from quality.diagnostics import Diagnostic
from quality.process import Command, ProcessError, run, sanitized
from quality.testing import SuiteRequest

TOOL = "browser worker"
BOOT = """\
import sys
sys.path.insert(0, sys.argv[1])
from quality.adapters.typescript.browserrun import main
main(int(sys.argv[2]))
"""


@dataclass(frozen=True, slots=True)
class BrowserJob:
    """The trusted paths, manifest, and suite to run in one network namespace."""

    node: Node
    playwright: Path
    browser: Path
    request: SuiteRequest
    output: Output
    base: str


@dataclass(frozen=True, slots=True)
class BrowserReply:
    """The browser's status and the owned server's independently recorded requests."""

    status: int = 1
    results: Path = Path("/")
    origin: str = ""
    missing: tuple[str, ...] = ()
    documents: int = 0
    tokens: tuple[str, ...] = ()
    error: Diagnostic | None = None


JOB = TypeAdapter(BrowserJob)
REPLY = TypeAdapter(BrowserReply)


def execute(job: BrowserJob) -> BrowserReply:
    """Serve and run inside the worker, closing the owned server on every exit."""
    request = job.request
    with serve(job.output, job.base, request.component.browser_settings.entries[0]) as server:
        values = settings(request, server.url, job.browser)
        status, results = run_playwright(job.node, job.playwright, request, values)
    return BrowserReply(
        status,
        results,
        server.origin,
        server.requests.missing(),
        server.requests.documents,
        tuple(server.requests.tokens),
    )


def main(expected_parent: int) -> None:
    """Read a JSON job from the parent and return observations or a structured tool failure."""
    job = JOB.validate_json(sys.stdin.buffer.read())
    try:
        with owned_processes():
            watch_parent(expected_parent)
            reply = execute(job)
    except ToolFailedError as error:
        reply = BrowserReply(error=replace(error.diagnostic, data=dict(error.diagnostic.data)))
    sys.stdout.buffer.write(REPLY.dump_json(reply))


def run_owned(job: BrowserJob) -> tuple[BrowserReply, str | None]:
    """Run a fresh worker with the same network restriction as unit suites."""
    argv = (
        sys.executable,
        "-I",
        "-c",
        BOOT,
        str(Path(__file__).resolve().parents[3]),
        str(os.getpid()),
    )
    argv, unisolated = suite_command(job.request, argv)
    command = Command(
        argv,
        cwd=job.request.directory,
        timeout=SUITE_TIMEOUT + 60,
        env=sanitized(),
        stdin=JOB.dump_json(job),
        reap=True,
    )
    try:
        completed = run(command)
    except ProcessError as error:
        raise failure(TOOL, str(error)) from error
    if completed.returncode != 0:
        fail(TOOL, completed.stderr_text() or completed.stdout_text())
    reply = REPLY.validate_json(completed.stdout)
    if reply.error is not None:
        raise ToolFailedError(reply.error)
    return reply, unisolated
