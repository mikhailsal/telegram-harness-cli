"""Test execution evidence: a suite passes only with a successful run of real tests (R06)."""

from __future__ import annotations

from dataclasses import dataclass

from quality.diagnostics import Diagnostic, Kind, Rule, Severity
from quality.testing import Outcome, Suite, SuiteResults, SuiteRun

FAILED = Rule(
    id="tests.failed",
    title="Tests failed",
    requirement="R06",
    explanation=(
        "A suite passes only when its runner exits successfully and no test fails or "
        "errors. Coverage written by a failed run is discarded, never evaluated."
    ),
    remedy="Fix the failing tests or the code they test, then rerun the check.",
)
NONE = Rule(
    id="tests.none",
    title="A mandatory suite ran no tests",
    requirement="R06",
    explanation=(
        "Every component declares unit test roots, and its unit suite must execute at "
        "least one test. A missing root, an empty selection, or only skipped tests fails."
    ),
    remedy="Add unit tests under the declared roots, or correct the roots in quality.toml.",
)
SKIPPED = Rule(
    id="tests.skipped",
    title="Tests were skipped or expected to fail",
    requirement="R06",
    explanation=(
        "Skipped tests and tests expected to fail do not count as executed; the suite "
        "still needs passing tests, and neither satisfies a scenario."
    ),
    remedy="Remove the skip or the expected-failure marker, or make the test runnable.",
    severity=Severity.WARNING,
)
FOCUSED = Rule(
    id="tests.focused",
    title="A focus marker limited the suite",
    requirement="R08",
    explanation=(
        "A focus marker, such as Dart's `solo`, makes the runner skip every test without "
        "one, so the suite did not run. Vitest's `.only` is refused by the runner itself."
    ),
    remedy="Remove the focus marker.",
)
EVIDENCE = Rule(
    id="tests.evidence",
    title="Test evidence is missing or invalid",
    requirement="R06",
    explanation=(
        "Results and coverage must come from this run: the reports are written to a new "
        "empty directory, must exist when the runner exits, must be parseable and "
        "consistent, and must name only files of the snapshot."
    ),
    remedy="Check the profile's test runner and configuration, then rerun the check.",
    kind=Kind.ENVIRONMENT,
)
NETWORK = Rule(
    id="tests.network",
    title="A contained test suite ran with network access",
    requirement="R06",
    explanation=(
        "Unit and owned browser suites run in a loopback-only namespace, so a test cannot "
        "reach a real service or the internet. This system does not let an unprivileged "
        "user create that namespace, so the suite ran with the network."
    ),
    remedy=(
        "Allow unprivileged user namespaces (on Ubuntu, set the sysctl "
        "kernel.apparmor_restrict_unprivileged_userns to 0), or review that these "
        "tests use no external network."
    ),
    severity=Severity.WARNING,
)
RULES = (FAILED, NONE, SKIPPED, FOCUSED, EVIDENCE, NETWORK)
FAILING = frozenset({Outcome.FAILED, Outcome.ERROR})
NOT_RUN = frozenset({Outcome.SKIPPED, Outcome.EXPECTED_FAILURE})
# A failing suite names at most this many of its failing cases.
NAMED = 5


def _failures(names: list[str]) -> str:
    if not names:
        return ""
    more = len(names) - NAMED
    return "; failed: " + ", ".join(names[:NAMED]) + (f", and {more} more" if more > 0 else "")


def _failed(run: SuiteRun, blocking: list[str], excused: list[str]) -> bool:
    """Whether the run failed; failures of quarantined tests alone explain a failure status."""
    if blocking or run.unhandled:
        return True
    return run.returncode != 0 and not (excused and run.returncode in run.failure_status)


def _not_run(where: str, results: SuiteResults, component: str) -> list[Diagnostic]:
    skipped, expected = results.count(Outcome.SKIPPED), results.count(Outcome.EXPECTED_FAILURE)
    parts = [f"skipped {skipped} tests"] if skipped else []
    parts += [f"expected {expected} tests to fail"] if expected else []
    if not parts:
        return []
    return [SKIPPED.diagnostic(f"{where} {' and '.join(parts)}", component=component)]


@dataclass(frozen=True, slots=True)
class _Tally:
    """What a run's cases count for: executed cases, and failures that block or are excused."""

    executed: int
    held: bool
    blocking: list[str]
    excused: list[str]


def _split(names: list[str], held: frozenset[str]) -> tuple[list[str], list[str]]:
    return [name for name in names if name not in held], [name for name in names if name in held]


def _tally(results: SuiteResults, quarantined: frozenset[str]) -> _Tally:
    trusted = [case for case in results.cases if case.name not in quarantined]
    failing = [case.name for case in results.cases if case.outcome in FAILING]
    blocking, excused = _split(failing, quarantined)
    executed = sum(1 for case in trusted if case.outcome not in NOT_RUN)
    return _Tally(executed, len(trusted) < len(results.cases), blocking, excused)


def _focused(where: str, results: SuiteResults, component: str) -> list[Diagnostic]:
    if not results.focused:
        return []
    message = f"{where} ran only focused tests and skipped the others"
    return [FOCUSED.diagnostic(message, component=component)]


def judge(
    component: str,
    suite: Suite,
    run: SuiteRun,
    results: SuiteResults,
    quarantined: frozenset[str] = frozenset(),
) -> list[Diagnostic]:
    """Report the run of one suite; its coverage may be used only if no finding is an error.

    A failure of a quarantined test does not fail the suite, and quarantined
    tests do not count as executed.
    """
    where = f"the {suite} suite of '{component}'"
    found = _focused(where, results, component)
    tally = _tally(results, quarantined)
    if tally.executed == 0:
        held = " besides quarantined ones" if tally.held else ""
        message = f"{where} executed no tests{held} (runner exit status {run.returncode})"
        found.append(NONE.diagnostic(message, component=component))
    elif _failed(run, tally.blocking, tally.excused):
        unhandled = f" and {run.unhandled} errors outside tests" if run.unhandled else ""
        message = (
            f"{where}: {len(tally.blocking)} of {tally.executed} tests failed{unhandled}; "
            f"runner exit status {run.returncode}{_failures(tally.blocking)}"
        )
        found.append(FAILED.diagnostic(message, component=component))
    found.extend(_not_run(where, results, component))
    return found


def missing_suite(component: str) -> Diagnostic:
    """Report a component without unit test roots."""
    message = f"'{component}' declares no unit test roots"
    return NONE.diagnostic(message, component=component)


def invalid_evidence(component: str, suite: Suite, problem: str) -> Diagnostic:
    """Report evidence that cannot be trusted; the run counts as not having passed."""
    return EVIDENCE.diagnostic(
        f"the {suite} suite of '{component}': {problem}", component=component
    )


def unisolated(component: str, reason: str, *, suite: Suite = Suite.UNIT) -> Diagnostic:
    """Report a contained suite that ran without network isolation."""
    message = f"the {suite} suite of '{component}' ran with the network: {reason}"
    return NETWORK.diagnostic(message, component=component)
