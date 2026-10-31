"""Unit coverage floors per component, compared exactly on integer counters (R06)."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass

from quality.coverage import Counter, CoverageReport, FileCoverage, total
from quality.diagnostics import Diagnostic, Kind, Location, Rule, Severity
from quality.policy import CoveragePolicy

LINE = Rule(
    id="coverage.line",
    title="Unit line coverage is below the minimum",
    requirement="R06",
    explanation=(
        "Covered executable lines divided by all expected executable source lines of the "
        "component, from its unit suite alone. Files the tests never import count as "
        "uncovered. The comparison is exact: covered * 100 >= minimum * total."
    ),
    remedy="Add unit tests for the uncovered code, or remove dead code.",
)
BRANCH = Rule(
    id="coverage.branch",
    title="Unit branch coverage is below the minimum",
    requirement="R06",
    explanation=(
        "Taken branch opportunities divided by all branch opportunities the native tool "
        "measures in the component's expected sources, from its unit suite alone."
    ),
    remedy="Add unit tests that take the missed branches.",
)
CHANGED_LINES = Rule(
    id="coverage.changed-lines",
    title="Coverage of new and changed lines is below the minimum",
    requirement="R06",
    explanation=(
        "Executable lines the candidate adds or changes in a component must be covered by its "
        "unit suite, even when the component totals pass."
    ),
    remedy="Add unit tests that execute the listed lines.",
)
NEW_FILE = Rule(
    id="coverage.new-file",
    title="A new source file has no unit coverage",
    requirement="R06",
    explanation="A source file the candidate adds with executable lines must not be 0% covered.",
    remedy="Add unit tests that execute the new file.",
)
UNACCOUNTED = Rule(
    id="coverage.unaccounted",
    title="An expected source file is missing from the coverage report",
    requirement="R06",
    explanation=(
        "The expected sources are derived from the snapshot, not from what the tests import. "
        "A file the report omits cannot be accounted for, so the coverage is incomplete."
    ),
    remedy=(
        "Configure the profile's coverage source to include every source root, or classify "
        "the file as generated or vendor with a reason in quality.toml."
    ),
)
NO_SOURCE = Rule(
    id="coverage.no-source",
    title="Coverage cannot be shown to be not applicable",
    requirement="R06",
    explanation=(
        "A component without expected source files is not applicable only when every "
        "declared source root exists; a source root without tracked files is a broken path."
    ),
    remedy="Correct the component's source roots in quality.toml.",
)
BRANCH_UNMEASURED = Rule(
    id="coverage.branch-unmeasured",
    title="The coverage report has no branch measurement",
    requirement="R06",
    explanation=(
        "Branch coverage is mandatory: neither a report without branch data nor a source file "
        "whose branches the tool did not measure can pass. Xdebug measures a PHP file only "
        "once it is loaded, and the gate loads only files that just declare (decision 0013)."
    ),
    remedy="Enable branch collection; a PHP file that runs code when loaded needs a test.",
    kind=Kind.ENVIRONMENT,
)
NOT_APPLICABLE = Rule(
    id="coverage.not-applicable",
    title="A coverage measurement is not applicable",
    requirement="R06",
    explanation=(
        "The component has no executable lines or no measurable branches in its expected "
        "sources, so the corresponding floor does not apply."
    ),
    remedy="No action is needed if the component really has nothing to measure.",
    severity=Severity.INFO,
)
RULES = (
    LINE,
    BRANCH,
    CHANGED_LINES,
    NEW_FILE,
    UNACCOUNTED,
    NO_SOURCE,
    BRANCH_UNMEASURED,
    NOT_APPLICABLE,
)

SHOWN_LINES = 20


@dataclass(frozen=True, slots=True)
class Expectation:
    """What one component's unit coverage must account for.

    `sources` are the repository paths of its expected source files and
    `missing_roots` its declared source roots without tracked files. `changed`
    maps paths to the lines the candidate adds or changes; `added` names the
    files it adds.
    """

    component: str
    sources: frozenset[str]
    missing_roots: tuple[str, ...]
    changed: Mapping[str, frozenset[int]]
    added: frozenset[str]


@dataclass(frozen=True, slots=True)
class ComponentCoverage:
    """The unit coverage of one component; `branch` is None when it was not measured."""

    component: str
    line: Counter
    branch: Counter | None
    changed: Counter


def _below(rule: Rule, what: str, counter: Counter, minimum: int, component: str) -> Diagnostic:
    message = (
        f"{what} coverage of '{component}' is {counter.describe()}, below the minimum of {minimum}%"
    )
    return rule.diagnostic(message, component=component, data=_counted(counter))


def _counted(counter: Counter) -> dict[str, str]:
    return {"covered": str(counter.covered), "total": str(counter.total)}


def _not_applicable(what: str, component: str) -> Diagnostic:
    message = f"{what} coverage of '{component}' is not applicable (0/0)"
    return NOT_APPLICABLE.diagnostic(message, component=component)


def _changed(files: list[FileCoverage], changed: Mapping[str, frozenset[int]]) -> Counter:
    counters = (
        Counter(
            len(changed.get(f.path, frozenset()) & f.covered),
            len(changed.get(f.path, frozenset()) & f.executable),
        )
        for f in files
    )
    return total(counters)


def _changed_finding(
    files: list[FileCoverage], expectation: Expectation, counter: Counter, minimum: int
) -> Diagnostic:
    changed = expectation.changed
    missed = [
        f"{file.path}:{line}"
        for file in files
        for line in sorted((changed.get(file.path, frozenset()) & file.executable) - file.covered)
    ]
    shown = ", ".join(missed[:SHOWN_LINES])
    more = f" and {len(missed) - SHOWN_LINES} more" if len(missed) > SHOWN_LINES else ""
    message = (
        f"changed-line coverage of '{expectation.component}' is {counter.describe()}, "
        f"below the minimum of {minimum}%; uncovered: {shown}{more}"
    )
    return CHANGED_LINES.diagnostic(
        message, component=expectation.component, data=_counted(counter)
    )


def _accounting(expectation: Expectation, report: CoverageReport) -> Iterator[Diagnostic]:
    component = expectation.component
    for path in sorted(expectation.sources - report.files.keys()):
        message = f"'{path}' of '{component}' is missing from the unit coverage report"
        yield UNACCOUNTED.diagnostic(message, location=Location(path), component=component)
    if not expectation.sources and expectation.missing_roots:
        roots = ", ".join(f"'{root}'" for root in expectation.missing_roots)
        message = f"'{component}' has no expected source files; missing source roots: {roots}"
        yield NO_SOURCE.diagnostic(message, component=component)


def _unmeasured(expectation: Expectation, files: list[FileCoverage]) -> Iterator[Diagnostic]:
    for file in (f for f in files if not f.branches_measured):
        message = f"the coverage tool did not measure the branches of '{file.path}'"
        yield BRANCH_UNMEASURED.diagnostic(
            message, location=Location(file.path), component=expectation.component
        )


def _new_files(expectation: Expectation, files: list[FileCoverage]) -> Iterator[Diagnostic]:
    for file in files:
        if file.path in expectation.added and file.executable and not file.covered:
            message = f"new file '{file.path}' has no unit coverage ({file.lines.describe()})"
            yield NEW_FILE.diagnostic(
                message, location=Location(file.path), component=expectation.component
            )


def _floors(summary: ComponentCoverage, minima: CoveragePolicy) -> Iterator[Diagnostic]:
    component = summary.component
    if not summary.line.applicable:
        yield _not_applicable("line", component)
    elif not summary.line.meets(minima.line):
        yield _below(LINE, "line", summary.line, minima.line, component)
    if summary.branch is None:
        message = f"the unit coverage report of '{component}' has no branch measurement"
        yield BRANCH_UNMEASURED.diagnostic(message, component=component)
    elif not summary.branch.applicable:
        yield _not_applicable("branch", component)
    elif not summary.branch.meets(minima.branch):
        yield _below(BRANCH, "branch", summary.branch, minima.branch, component)


def evaluate(
    expectation: Expectation, report: CoverageReport, minima: CoveragePolicy
) -> tuple[ComponentCoverage, list[Diagnostic]]:
    """Check one component's unit coverage against every floor of the policy.

    Only the expected sources count; whatever else the report lists, such as test
    files or another component's files, is ignored.
    """
    files = [report.files[path] for path in sorted(expectation.sources & report.files.keys())]
    branch = total(f.branches for f in files) if report.branches_measured else None
    summary = ComponentCoverage(
        expectation.component,
        total(f.lines for f in files),
        branch,
        _changed(files, expectation.changed),
    )
    found = [*_accounting(expectation, report), *_unmeasured(expectation, files)]
    if expectation.sources or not expectation.missing_roots:
        found.extend(_floors(summary, minima))
    if summary.changed.applicable and not summary.changed.meets(minima.changed_lines):
        found.append(_changed_finding(files, expectation, summary.changed, minima.changed_lines))
    if minima.new_file_must_be_covered:
        found.extend(_new_files(expectation, files))
    return summary, found
