"""Normalized test results, the records of one suite run, and the static gates' request.

Both requests live here, below the adapters, because the engine builds them and
every profile adapter receives them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType

from quality.boundedio import ReportError
from quality.diagnostics import Diagnostic
from quality.policy import Component, Suite


class Outcome(StrEnum):
    """The result of one test case; an expected failure ran but proves nothing."""

    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    SKIPPED = "skipped"
    EXPECTED_FAILURE = "expected-failure"


# The evidence of a case whose test attached a valid accessibility scan in every viewport.
ACCESSIBILITY_SCAN = "accessibility-scan"


@dataclass(frozen=True, slots=True)
class CaseResult:
    """One executed or skipped test case.

    `evidence` names what the runner proved the case produced beside its
    outcome, such as a valid accessibility scan; some requirements need it.
    """

    name: str
    outcome: Outcome
    evidence: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class SuiteResults:
    """The test cases of one suite run.

    `focused` says that a focus marker, such as Dart's `solo`, made the runner
    skip every test without one.
    """

    cases: tuple[CaseResult, ...]
    focused: bool = False

    def count(self, *outcomes: Outcome) -> int:
        """Count the cases with any of the outcomes."""
        return sum(1 for case in self.cases if case.outcome in outcomes)

    @property
    def executed(self) -> int:
        """Cases that ran to a result of their own: neither skipped nor expected to fail."""
        return len(self.cases) - self.count(Outcome.SKIPPED, Outcome.EXPECTED_FAILURE)

    def annotated(
        self,
        inverted: frozenset[str],
        retried: frozenset[str],
        invalidated: frozenset[str] = frozenset(),
    ) -> SuiteResults:
        """Apply what a runner reports beside its results.

        `inverted` cases pass when they fail as expected, such as Vitest's
        `test.fails`, so their passes are expected failures. `retried` cases
        passed only on a retry, which proves them flaky, so their passes are
        failures. `invalidated` cases lack independent runner evidence and fail
        even when the native report says they passed. Raise ReportError for unknown names.
        """
        unknown = sorted((inverted | retried | invalidated) - {case.name for case in self.cases})
        if unknown:
            msg = f"annotated tests are not among the results: {', '.join(unknown)}"
            raise ReportError(msg)
        cases = tuple(
            CaseResult(case.name, _annotated(case.name, inverted, retried | invalidated))
            if case.outcome is Outcome.PASSED
            else case
            for case in self.cases
        )
        return SuiteResults(cases, self.focused)

    def with_evidence(self, evidence: Mapping[str, frozenset[str]]) -> SuiteResults:
        """Record the evidence a runner reports for its cases.

        Raise ReportError when a name is not a case of the results.
        """
        unknown = sorted(set(evidence) - {case.name for case in self.cases})
        if unknown:
            msg = f"evidence names tests that are not among the results: {', '.join(unknown)}"
            raise ReportError(msg)
        cases = tuple(
            CaseResult(
                case.name, case.outcome, case.evidence | evidence.get(case.name, frozenset())
            )
            for case in self.cases
        )
        return SuiteResults(cases, self.focused)


def _annotated(name: str, inverted: frozenset[str], retried: frozenset[str]) -> Outcome:
    if name in retried:
        return Outcome.FAILED
    return Outcome.EXPECTED_FAILURE if name in inverted else Outcome.PASSED


@dataclass(frozen=True, slots=True)
class SuiteRequest:
    """One suite to run: the component, its test roots, and a new empty output directory.

    `started` is the time the output directory was created; reports must not be older.
    `work` is the snapshot's private work area, where runners keep environments.
    `tree` names the candidate's tree, and `files` are the repository paths the
    component owns in it, for runners that build the candidate.
    """

    component: Component
    suite: Suite
    roots: tuple[str, ...]
    snapshot: Path
    output: Path
    started: float
    work: Path
    tree: str = ""
    files: tuple[str, ...] = ()

    @property
    def directory(self) -> Path:
        """The component root inside the snapshot, where native tools run."""
        return self.snapshot / self.component.root


@dataclass(frozen=True, slots=True)
class StaticRequest:
    """The files of one prepared component that its static gates check, in a snapshot.

    `files` are the component's source and test files; `configs` are its
    configuration files written in a programming language, such as
    `eslint.config.js`, which formatters check too; `resources` are its other
    files except generated ones, such as stylesheets, HTML, images, and data.
    Paths are repository paths.
    """

    component: Component
    snapshot: Path
    work: Path
    files: tuple[str, ...]
    configs: tuple[str, ...] = ()
    resources: tuple[str, ...] = ()

    @property
    def directory(self) -> Path:
        """The component root inside the snapshot, where native tools run."""
        return self.snapshot / self.component.root


class ResultsFormat(StrEnum):
    """The format of a runner's results file, which decides the parser the engine uses."""

    JUNIT = "junit"
    DART_JSON = "dart-json"
    PLAYWRIGHT = "playwright-json"


FAILURE_STATUS = frozenset({1})


def _no_evidence() -> Mapping[str, frozenset[str]]:
    return MappingProxyType({})


@dataclass(frozen=True, slots=True)
class SuiteRun:
    """What a runner reports back: its exit status and the reports it wrote.

    `unisolated` says why a unit or owned browser suite ran with the network;
    it is None when the suite reached loopback only, and for other suites. `inverted` names
    the cases the runner reports as passing when they fail as expected,
    `retried` the cases that passed only on a retry, `unhandled` counts errors
    raised outside any test, and `failure_status` holds the exit statuses with
    which the runner reports failed tests. `evidence` maps case names to what
    they proved beside their outcome, `findings` are what the runner found
    beside the tests, such as console errors of a browser, and `reports` are
    further evidence files of the run. `invalidated` cases fail because the
    runner could not independently verify their native passing results.
    """

    returncode: int
    results: Path
    coverage: Path | None = None
    results_format: ResultsFormat = ResultsFormat.JUNIT
    unisolated: str | None = None
    inverted: frozenset[str] = frozenset()
    retried: frozenset[str] = frozenset()
    unhandled: int = 0
    failure_status: frozenset[int] = FAILURE_STATUS
    evidence: Mapping[str, frozenset[str]] = field(default_factory=_no_evidence)
    findings: tuple[Diagnostic, ...] = ()
    reports: tuple[Path, ...] = ()
    invalidated: frozenset[str] = frozenset()


def suite_roots(component: Component, suite: Suite) -> tuple[str, ...]:
    """Return the test roots a component declares for a suite, relative to its root."""
    return tuple(component.tests.of(suite))
