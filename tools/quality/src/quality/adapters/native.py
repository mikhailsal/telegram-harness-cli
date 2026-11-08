"""Run native tools and turn their failures into environment diagnostics."""

from __future__ import annotations

from collections.abc import Collection
from typing import NoReturn

from quality.diagnostics import Diagnostic, Kind, Rule
from quality.process import Command, Completed, ProcessError, run
from quality.process.isolation import contained
from quality.testing import Suite, SuiteRequest

FAILED = Rule(
    id="tools.failed",
    title="A native tool failed or produced unusable output",
    requirement="R02",
    explanation=(
        "A check whose tool crashes, times out, exits with an unexpected status, or writes "
        "malformed output did not run, so the gate cannot pass."
    ),
    remedy="Read the tool output in the message, fix the environment, and run the check again.",
    kind=Kind.ENVIRONMENT,
)
RULES = (FAILED,)

EXCERPT = 2000


class ToolFailedError(Exception):
    """A native tool did not produce a usable result."""

    def __init__(self, diagnostic: Diagnostic) -> None:
        super().__init__(diagnostic.message)
        self.diagnostic = diagnostic


def failure(name: str, detail: str) -> ToolFailedError:
    """Build the failure for one tool with a bounded excerpt of its output."""
    text = detail.strip()
    if len(text) > EXCERPT:
        text = text[:EXCERPT] + " [truncated]"
    return ToolFailedError(FAILED.diagnostic(f"{name}: {text}" if text else f"{name} failed"))


def fail(name: str, detail: str) -> NoReturn:
    """Raise the failure of one tool."""
    raise failure(name, detail)


def run_tool(command: Command, *, name: str, accepted: Collection[int]) -> Completed:
    """Run a tool and require one of the accepted exit statuses."""
    try:
        completed = run(command)
    except ProcessError as error:
        raise failure(name, str(error)) from error
    if completed.returncode not in accepted:
        output = completed.stderr_text() or completed.stdout_text()
        raise failure(name, f"exit status {completed.returncode}: {output}")
    return completed


def suite_command(
    request: SuiteRequest, argv: tuple[str, ...]
) -> tuple[tuple[str, ...], str | None]:
    """Return a unit or owned browser command reaching loopback only when possible.

    The second item says why a contained suite runs with the network (R06.9).
    """
    browser = request.component.in_browser and request.suite is Suite.E2E
    if request.suite is not Suite.UNIT and not browser:
        return argv, None
    return contained(argv)
