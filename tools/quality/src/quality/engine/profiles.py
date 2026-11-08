"""What the engine needs from a language profile adapter."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from quality.coverage import CoverageReport
from quality.diagnostics import Diagnostic, Rule
from quality.metrics import Measurements
from quality.policy import Component
from quality.testing import StaticRequest, SuiteRequest, SuiteRun


class ProfileTests(Protocol):
    """A profile's test runner; it writes its reports only into the request's output."""

    def prepare(self, component: Component, snapshot: Path, work: Path) -> list[Diagnostic]:
        """Check the component's native configuration and make its locked environment ready.

        Return configuration findings; after an error nothing else runs for the
        component. Raise ToolFailedError when the environment cannot be made ready.
        """
        ...

    def expected(self, path: str) -> bool:
        """Whether a source file must appear in this profile's coverage report."""
        ...

    def run(self, request: SuiteRequest) -> SuiteRun:
        """Run one suite with coverage; raise ToolFailedError when it cannot run at all."""
        ...

    def coverage(self, data: bytes, request: SuiteRequest) -> CoverageReport:
        """Parse the coverage report of a run; raise ReportError when it is unusable."""
        ...


class ProfileAdapter(ProfileTests, Protocol):
    """A profile's native configuration, format, lint, and type gates plus its test runner."""

    @property
    def rules(self) -> tuple[Rule, ...]:
        """The rules `static` reports."""
        ...

    def static(self, request: StaticRequest) -> list[Diagnostic]:
        """Check a prepared component's source and test files with its native tools."""
        ...

    def measure(self, request: StaticRequest) -> Measurements | None:
        """Measure the structure of a prepared component's files with the language's parser.

        Return None when the common gates measure the profile's files themselves.
        Raise ToolFailedError when the parser cannot run.
        """
        ...


class ProfileSetup(Protocol):
    """What `bootstrap` and `doctor` need from a profile, in the working tree."""

    def provision(self, component: Component, root: Path) -> list[Diagnostic]:
        """Download what offline checks of the component need; return configuration findings.

        Raise ToolFailedError when a download or installation fails.
        """
        ...

    def probe(self, component: Component, root: Path) -> list[Diagnostic]:
        """Confirm, without downloading, that checks of the component can run offline.

        Raise ToolFailedError when they cannot.
        """
        ...
