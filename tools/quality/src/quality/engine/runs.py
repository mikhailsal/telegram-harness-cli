"""Suite runs and their evidence: fresh reports from a new output directory (R06).

Suites run in the isolated snapshot through a profile's runner. Results and
coverage are read only from a new output directory created for that run, and
coverage is evaluated only after a clean unit run (decision 0008).
"""

from __future__ import annotations

import tempfile
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from quality.adapters.native import ToolFailedError
from quality.boundedio import ReportError, read_bounded
from quality.coverage import CoverageReport
from quality.diagnostics import Diagnostic, Severity
from quality.discovery import Region
from quality.discovery.classify import under
from quality.engine.profiles import ProfileTests
from quality.engine.scope import Scope
from quality.policy import Category, Component, Policy, Quarantine
from quality.reports import Evidence
from quality.rules import coverage, quarantine, testing
from quality.scenarios.reconcile import Ran
from quality.testing import (
    ResultsFormat,
    Suite,
    SuiteRequest,
    SuiteResults,
    SuiteRun,
    parse_dart_json,
    parse_junit,
    parse_playwright,
    suite_roots,
)

# File systems with coarse timestamps may date a new report up to this many seconds early.
FRESHNESS_SLACK = 2.0
PARSERS: dict[ResultsFormat, Callable[[bytes, str], SuiteResults]] = {
    ResultsFormat.JUNIT: parse_junit,
    ResultsFormat.DART_JSON: parse_dart_json,
    ResultsFormat.PLAYWRIGHT: parse_playwright,
}


def read_evidence(path: Path, request: SuiteRequest) -> bytes:
    """Read a report of this run: inside its new output directory and not older than it."""
    if not path.resolve().is_relative_to(request.output.resolve()):
        msg = f"report '{path.name}' is outside the output directory of the run"
        raise ReportError(msg)
    data = read_bounded(path)
    if path.stat().st_mtime < request.started - FRESHNESS_SLACK:
        msg = f"report '{path.name}' is older than the run"
        raise ReportError(msg)
    return data


@dataclass(frozen=True, slots=True)
class SuiteOutcome:
    """One suite run: its findings, the run if it is clean, and its results if they are valid.

    `reports` are the native reports the runner wrote, whatever their validity.
    """

    diagnostics: list[Diagnostic]
    run: SuiteRun | None = None
    results: SuiteResults | None = None
    reports: tuple[Path, ...] = ()


def native_reports(run: SuiteRun | None) -> tuple[Path, ...]:
    """Return the reports a runner wrote, for the reports directory of a gate run."""
    if run is None:
        return ()
    native = (run.results,) if run.coverage is None else (run.results, run.coverage)
    return (*native, *run.reports)


def run_suite(
    runner: ProfileTests, request: SuiteRequest, held: Sequence[Quarantine] = ()
) -> SuiteOutcome:
    """Run one suite and judge its results; `held` are the suite's current quarantine entries."""
    component = request.component.id
    run: SuiteRun | None = None
    try:
        run = runner.run(request)
        parse = PARSERS[run.results_format]
        results = parse(read_evidence(run.results, request), "test results")
        results = results.annotated(run.inverted, run.retried, run.invalidated).with_evidence(
            run.evidence
        )
    except ToolFailedError as error:
        return SuiteOutcome([error.diagnostic], reports=native_reports(run))
    except ReportError as error:
        problem = testing.invalid_evidence(component, request.suite, str(error))
        return SuiteOutcome([problem], reports=native_reports(run))
    names = frozenset(entry.test for entry in held)
    found = testing.judge(component, request.suite, run, results, names)
    found.extend(quarantine.reported(held, results))
    found.extend(run.findings)
    if run.unisolated is not None:
        found.append(testing.unisolated(component, run.unisolated, suite=request.suite))
    clean = not any(diagnostic.severity is Severity.ERROR for diagnostic in found)
    return SuiteOutcome(found, run if clean else None, results, native_reports(run))


@dataclass(frozen=True, slots=True)
class UnitCoverage:
    """The coverage findings of a unit run, its summary, and the normalized report."""

    diagnostics: list[Diagnostic]
    summary: coverage.ComponentCoverage | None = None
    report: CoverageReport | None = None


def unit_coverage(
    runner: ProfileTests,
    request: SuiteRequest,
    run: SuiteRun,
    expected: coverage.Expectation,
    policy: Policy,
) -> UnitCoverage:
    """Evaluate the coverage of a clean unit run against the policy floors."""
    component = request.component.id
    if run.coverage is None:
        problem = "the runner wrote no coverage report"
        return UnitCoverage([testing.invalid_evidence(component, Suite.UNIT, problem)])
    try:
        report = runner.coverage(read_evidence(run.coverage, request), request)
    except ReportError as error:
        return UnitCoverage([testing.invalid_evidence(component, Suite.UNIT, str(error))])
    summary, found = coverage.evaluate(expected, report, policy.coverage)
    return UnitCoverage(found, summary, report)


@dataclass(frozen=True, slots=True)
class SuiteFindings:
    """What the suites of the prepared components produced, and their results by component."""

    diagnostics: list[Diagnostic] = field(default_factory=list[Diagnostic])
    coverage: list[coverage.ComponentCoverage] = field(
        default_factory=list[coverage.ComponentCoverage]
    )
    ran: dict[str, Ran] = field(default_factory=dict[str, Ran])
    evidence: list[Evidence] = field(default_factory=list[Evidence])


def expectation(component: Component, scope: Scope, runner: ProfileTests) -> coverage.Expectation:
    """Derive the expected sources from the snapshot, independently of what tests import."""
    sources = frozenset(
        record.path
        for record in scope.inventory.files
        if record.component == component.id
        and record.region is Region.SOURCE
        and record.category is Category.SOURCE
        and runner.expected(record.path)
    )
    prefix = "" if component.root == "." else component.root + "/"
    missing = tuple(
        root
        for root in component.source
        if not any(under(entry.path, prefix + root) for entry in scope.snapshot.entries)
    )
    changes = scope.changes
    return coverage.Expectation(component.id, sources, missing, changes.lines, changes.added)


def request(scope: Scope, component: Component, suite: Suite) -> SuiteRequest:
    """Build a request with a new, empty output directory."""
    snapshot = scope.snapshot
    started = time.time()
    output = Path(tempfile.mkdtemp(prefix=f"{component.id}-{suite}-", dir=snapshot.work))
    roots = suite_roots(component, suite)
    files = tuple(
        record.path for record in scope.inventory.files if record.component == component.id
    )
    return SuiteRequest(
        component, suite, roots, snapshot.root, output, started, snapshot.work, snapshot.tree, files
    )


def component_suites(
    component: Component,
    runner: ProfileTests,
    scope: Scope,
    suites: Iterable[Suite],
    held: Sequence[Quarantine] = (),
) -> SuiteFindings:
    """Run a component's suites; only its own unit run can satisfy its coverage floors."""
    findings = SuiteFindings()
    ran: dict[Suite, SuiteResults | None] = {}
    for suite in suites:
        if not suite_roots(component, suite):
            if suite is Suite.UNIT:
                findings.diagnostics.append(testing.missing_suite(component.id))
            continue
        current = request(scope, component, suite)
        outcome = run_suite(runner, current, quarantine.quarantined(held, component.id, suite))
        findings.diagnostics.extend(outcome.diagnostics)
        ran[suite] = outcome.results
        measured = None
        if suite is Suite.UNIT and outcome.run is not None:
            expected = expectation(component, scope, runner)
            measured = unit_coverage(runner, current, outcome.run, expected, scope.policy)
            findings.diagnostics.extend(measured.diagnostics)
            if measured.summary is not None:
                findings.coverage.append(measured.summary)
        report = None if measured is None else measured.report
        evidence = Evidence(component.id, suite, outcome.results, report, outcome.reports)
        findings.evidence.append(evidence)
    findings.ran[component.id] = ran
    return findings


def suite_findings(
    scope: Scope,
    ready: Iterable[tuple[Component, ProfileTests]],
    plan: Callable[[Component], Iterable[Suite]],
    held: Sequence[Quarantine] = (),
) -> SuiteFindings:
    """Run the suites each prepared component's plan names, and collect what they produced."""
    findings = SuiteFindings()
    for component, runner in ready:
        own = component_suites(component, runner, scope, plan(component), held)
        findings.diagnostics.extend(own.diagnostics)
        findings.coverage.extend(own.coverage)
        findings.ran.update(own.ran)
        findings.evidence.extend(own.evidence)
    return findings
