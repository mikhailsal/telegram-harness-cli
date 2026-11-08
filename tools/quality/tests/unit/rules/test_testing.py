from pathlib import Path

import pytest

from quality.diagnostics import Diagnostic, Kind, Severity
from quality.rules.testing import (
    EVIDENCE,
    FAILED,
    FOCUSED,
    NETWORK,
    NONE,
    RULES,
    SKIPPED,
    invalid_evidence,
    judge,
    missing_suite,
    unisolated,
)
from quality.testing import CaseResult, Outcome, Suite, SuiteResults, SuiteRun

PASSED = Outcome.PASSED


def run(
    returncode: int, *, unhandled: int = 0, status: frozenset[int] = frozenset({1})
) -> SuiteRun:
    return SuiteRun(returncode, Path("results.xml"), unhandled=unhandled, failure_status=status)


def results(*outcomes: Outcome) -> SuiteResults:
    return SuiteResults(tuple(CaseResult(f"t{i}", o) for i, o in enumerate(outcomes)))


def summary(found: list[Diagnostic]) -> list[tuple[str, Severity, str]]:
    return [(d.rule, d.severity, d.message) for d in found]


def test_a_clean_run_has_no_findings() -> None:
    assert judge("app", Suite.UNIT, run(0), results(PASSED, PASSED)) == []


@pytest.mark.parametrize(
    "found",
    [results(), results(Outcome.SKIPPED), results(Outcome.SKIPPED, Outcome.SKIPPED)],
    ids=["empty", "one-skipped", "all-skipped"],
)
def test_a_run_that_executes_nothing_fails(found: SuiteResults) -> None:
    diagnostics = judge("app", Suite.UNIT, run(0), found)
    assert diagnostics[0].rule == NONE.id
    assert (
        diagnostics[0].message == "the unit suite of 'app' executed no tests (runner exit status 0)"
    )
    assert diagnostics[0].component == "app"
    skipped = [d.rule for d in diagnostics[1:]]
    assert skipped == ([SKIPPED.id] if found.cases else [])


def test_an_empty_run_reports_the_runner_status() -> None:
    found = judge("app", Suite.UNIT, run(5), results())
    assert summary(found) == [
        (
            NONE.id,
            Severity.ERROR,
            "the unit suite of 'app' executed no tests (runner exit status 5)",
        )
    ]


@pytest.mark.parametrize(
    ("outcomes", "named"),
    [
        ((Outcome.FAILED, PASSED), "t0"),
        ((PASSED, Outcome.ERROR, Outcome.FAILED), "t1, t2"),
        ((Outcome.FAILED,) * 7, "t0, t1, t2, t3, t4, and 2 more"),
    ],
)
def test_failing_cases_fail_the_suite_and_are_named(
    outcomes: tuple[Outcome, ...], named: str
) -> None:
    found = judge("app", Suite.INTEGRATION, run(1), results(*outcomes))
    failing = sum(outcome is not PASSED for outcome in outcomes)
    message = (
        f"the integration suite of 'app': {failing} of {len(outcomes)} tests failed; "
        f"runner exit status 1; failed: {named}"
    )
    assert summary(found) == [(FAILED.id, Severity.ERROR, message)]


def test_a_failed_runner_fails_the_suite_even_when_every_case_passed() -> None:
    found = judge("app", Suite.UNIT, run(2), results(PASSED))
    message = "the unit suite of 'app': 0 of 1 tests failed; runner exit status 2"
    assert summary(found) == [(FAILED.id, Severity.ERROR, message)]


def test_failing_cases_fail_the_suite_even_when_the_runner_exits_zero() -> None:
    found = judge("app", Suite.UNIT, run(0), results(Outcome.FAILED))
    assert [d.rule for d in found] == [FAILED.id]


def test_skipped_cases_warn_alongside_passing_ones() -> None:
    found = judge("app", Suite.UNIT, run(0), results(PASSED, Outcome.SKIPPED, Outcome.SKIPPED))
    assert summary(found) == [
        (SKIPPED.id, Severity.WARNING, "the unit suite of 'app' skipped 2 tests"),
    ]


def test_skipped_cases_warn_alongside_failures() -> None:
    found = judge("app", Suite.UNIT, run(1), results(Outcome.FAILED, Outcome.SKIPPED))
    assert [(d.rule, d.severity) for d in found] == [
        (FAILED.id, Severity.ERROR),
        (SKIPPED.id, Severity.WARNING),
    ]


def test_a_component_without_unit_roots_has_no_suite() -> None:
    found = missing_suite("lib")
    assert (found.rule, found.severity, found.component) == (NONE.id, Severity.ERROR, "lib")
    assert found.message == "'lib' declares no unit test roots"


def test_invalid_evidence_is_an_environment_error() -> None:
    found = invalid_evidence("lib", Suite.UNIT, "report 'junit.xml' is older than the run")
    assert (found.rule, found.kind, found.severity) == (
        EVIDENCE.id,
        Kind.ENVIRONMENT,
        Severity.ERROR,
    )
    assert found.message == "the unit suite of 'lib': report 'junit.xml' is older than the run"
    assert found.component == "lib"
    assert found.location is None


def test_the_rules_are_registered_with_their_requirement() -> None:
    assert [rule.id for rule in RULES] == [
        "tests.failed",
        "tests.none",
        "tests.skipped",
        "tests.focused",
        "tests.evidence",
        "tests.network",
    ]
    assert [rule.requirement for rule in RULES] == ["R06", "R06", "R06", "R08", "R06", "R06"]
    assert [rule.kind for rule in RULES] == [
        Kind.VIOLATION,
        Kind.VIOLATION,
        Kind.VIOLATION,
        Kind.VIOLATION,
        Kind.ENVIRONMENT,
        Kind.VIOLATION,
    ]


def test_a_suite_without_network_isolation_warns() -> None:
    found = unisolated("lib", "network isolation is unavailable: [Errno 1] denied")
    assert (found.rule, found.severity, found.component) == (NETWORK.id, Severity.WARNING, "lib")
    assert found.message == (
        "the unit suite of 'lib' ran with the network: "
        "network isolation is unavailable: [Errno 1] denied"
    )


def test_expected_failures_warn_and_do_not_count_as_executed() -> None:
    found = judge("app", Suite.UNIT, run(0), results(Outcome.EXPECTED_FAILURE))
    assert summary(found) == [
        (
            NONE.id,
            Severity.ERROR,
            "the unit suite of 'app' executed no tests (runner exit status 0)",
        ),
        (SKIPPED.id, Severity.WARNING, "the unit suite of 'app' expected 1 tests to fail"),
    ]
    found = judge(
        "app", Suite.UNIT, run(0), results(PASSED, Outcome.SKIPPED, Outcome.EXPECTED_FAILURE)
    )
    assert summary(found) == [
        (
            SKIPPED.id,
            Severity.WARNING,
            "the unit suite of 'app' skipped 1 tests and expected 1 tests to fail",
        )
    ]


def test_a_focused_run_fails_even_when_its_tests_pass() -> None:
    found = judge("app", Suite.UNIT, run(0), SuiteResults(results(PASSED).cases, focused=True))
    assert summary(found) == [
        (
            FOCUSED.id,
            Severity.ERROR,
            "the unit suite of 'app' ran only focused tests and skipped the others",
        )
    ]


def test_errors_outside_tests_fail_a_suite_whose_tests_passed() -> None:
    found = judge("app", Suite.UNIT, run(0, unhandled=2), results(PASSED))
    message = (
        "the unit suite of 'app': 0 of 1 tests failed and 2 errors outside tests; "
        "runner exit status 0"
    )
    assert summary(found) == [(FAILED.id, Severity.ERROR, message)]


def test_a_quarantined_failure_does_not_fail_the_suite() -> None:
    found = judge(
        "app", Suite.INTEGRATION, run(1), results(PASSED, Outcome.FAILED), frozenset({"t1"})
    )
    assert found == []


def test_quarantined_tests_do_not_count_as_executed() -> None:
    found = judge("app", Suite.INTEGRATION, run(0), results(PASSED), frozenset({"t0"}))
    message = "the integration suite of 'app' executed no tests besides quarantined ones"
    assert summary(found) == [(NONE.id, Severity.ERROR, f"{message} (runner exit status 0)")]


@pytest.mark.parametrize(
    ("returncode", "status", "quarantined", "outcomes"),
    [
        # The runner's status is not one it uses for failed tests: it crashed or was interrupted.
        (2, frozenset({1}), frozenset({"t1"}), (PASSED, Outcome.FAILED)),
        # A failure status with no failing quarantined test is not explained by the quarantine.
        (1, frozenset({1}), frozenset({"t1"}), (PASSED, PASSED)),
        # A quarantined failure does not excuse a failure beside it.
        (1, frozenset({1}), frozenset({"t1"}), (Outcome.FAILED, Outcome.FAILED)),
    ],
)
def test_a_quarantine_excuses_only_the_failures_it_holds(
    returncode: int,
    status: frozenset[int],
    quarantined: frozenset[str],
    outcomes: tuple[Outcome, ...],
) -> None:
    found = judge(
        "app", Suite.INTEGRATION, run(returncode, status=status), results(*outcomes), quarantined
    )
    assert [d.rule for d in found] == [FAILED.id]


def test_a_runner_may_report_failures_with_several_statuses() -> None:
    held = frozenset({"t1"})
    errored = results(PASSED, Outcome.ERROR)
    assert judge("app", Suite.INTEGRATION, run(2, status=frozenset({1, 2})), errored, held) == []


def test_a_browser_suite_without_network_isolation_names_its_own_suite() -> None:
    found = unisolated("web", "namespaces denied", suite=Suite.E2E)
    assert found.message == "the e2e suite of 'web' ran with the network: namespaces denied"
