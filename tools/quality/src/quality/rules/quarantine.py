"""Quarantine: a flaky test's failure is reported but tolerated until the entry expires (R08.7).

Each `[[quarantine]]` entry of quality.toml names one test case of one
non-unit suite, with a reason, an owner, and an expiry date. While it lasts,
the case's failure does not fail its suite, the case proves no scenario, and
every run reports it. An expired entry no longer applies and fails the check;
an entry whose test is not among the results of its suite fails it too.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from quality.diagnostics import Diagnostic, Location, Rule, Severity
from quality.policy import Quarantine, Suite
from quality.testing import Outcome, SuiteResults

QUARANTINED = Rule(
    id="tests.quarantined",
    title="A quarantined test ran",
    requirement="R08",
    explanation=(
        "A quarantined test runs with its suite, but its failure does not fail the suite "
        "and it proves no scenario until the quarantine entry expires."
    ),
    remedy="Fix the flaky test, then remove its quarantine entry.",
    severity=Severity.WARNING,
)
EXPIRED = Rule(
    id="quarantine.expired",
    title="A quarantine entry has expired",
    requirement="R08",
    explanation=(
        "A quarantine lasts until its `expires` date. After that the entry no longer "
        "applies, so the test's failures fail its suite again."
    ),
    remedy="Fix the test and remove the entry, or review the quarantine and extend it.",
)
UNUSED = Rule(
    id="quarantine.unused",
    title="A quarantined test is not among its suite's results",
    requirement="R08",
    explanation=(
        "A quarantine entry names one case of one suite. When that suite ran without the "
        "case, the test was renamed or removed and the entry is stale."
    ),
    remedy="Remove the entry, or correct the test name.",
)
RULES = (QUARANTINED, EXPIRED, UNUSED)
POLICY = Location("quality.toml")
VERBS = {
    Outcome.PASSED: "passed",
    Outcome.FAILED: "failed",
    Outcome.ERROR: "raised an error",
    Outcome.SKIPPED: "was skipped",
    Outcome.EXPECTED_FAILURE: "was expected to fail",
}


def current(
    entries: Iterable[Quarantine], today: date
) -> tuple[list[Quarantine], list[Diagnostic]]:
    """Split the entries into those that still apply and findings for the expired ones."""
    active: list[Quarantine] = []
    found: list[Diagnostic] = []
    for entry in entries:
        if entry.expires >= today:
            active.append(entry)
            continue
        message = f"the quarantine of '{entry.test}' ({entry.suite}) expired on {entry.expires}"
        found.append(EXPIRED.diagnostic(message, location=POLICY, component=entry.component))
    return active, found


def quarantined(entries: Iterable[Quarantine], component: str, suite: Suite) -> list[Quarantine]:
    """Return the entries of one suite of one component."""
    return [entry for entry in entries if entry.component == component and entry.suite is suite]


def reported(entries: Iterable[Quarantine], results: SuiteResults) -> list[Diagnostic]:
    """Report each quarantined case the suite ran, and each entry whose case it did not run."""
    outcomes = {case.name: case.outcome for case in results.cases}
    found: list[Diagnostic] = []
    for entry in entries:
        where = f"the {entry.suite} suite of '{entry.component}'"
        outcome = outcomes.get(entry.test)
        if outcome is None:
            message = f"{where} has no test '{entry.test}', which is quarantined"
            found.append(UNUSED.diagnostic(message, location=POLICY, component=entry.component))
            continue
        message = (
            f"{where}: the quarantined test '{entry.test}' {VERBS[outcome]}; "
            f"ignored until {entry.expires} ({entry.reason})"
        )
        found.append(QUARANTINED.diagnostic(message, component=entry.component))
    return found
