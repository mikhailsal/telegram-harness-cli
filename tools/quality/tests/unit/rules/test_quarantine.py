from datetime import date

from quality.diagnostics import Location, Severity
from quality.policy import Quarantine, Suite
from quality.rules.exceptions import FIXED_PREFIXES
from quality.rules.quarantine import RULES, current, quarantined, reported
from quality.testing import CaseResult, Outcome, SuiteResults
from quality.testkit.policies import ComponentSpec, build_policy, quarantine_toml

POLICY = Location("quality.toml")


def entries(*tables: str) -> list[Quarantine]:
    spec = ComponentSpec(integration=("integration",), e2e=("e2e",))
    other = ComponentSpec(id="lib", root="lib", integration=("integration",))
    return build_policy(spec, other, extra="\n".join(tables)).quarantine


def test_an_entry_applies_through_its_expiry_date() -> None:
    held = entries(
        quarantine_toml("a", expires="2026-10-21"),
        quarantine_toml("b", expires="2026-10-20", suite="e2e"),
    )
    active, found = current(held, date(2026, 10, 21))
    assert [entry.test for entry in active] == ["a"]
    assert [(d.rule, d.severity, d.message, d.location, d.component) for d in found] == [
        (
            "quarantine.expired",
            Severity.ERROR,
            "the quarantine of 'b' (e2e) expired on 2026-10-20",
            POLICY,
            "app",
        )
    ]


def test_entries_are_selected_by_component_and_suite() -> None:
    held = entries(
        quarantine_toml("a"),
        quarantine_toml("b", suite="e2e"),
        quarantine_toml("c", component="lib"),
    )
    assert [entry.test for entry in quarantined(held, "app", Suite.INTEGRATION)] == ["a"]
    assert [entry.test for entry in quarantined(held, "lib", Suite.INTEGRATION)] == ["c"]
    assert quarantined(held, "lib", Suite.E2E) == []


def test_every_quarantined_case_that_ran_is_reported() -> None:
    held = entries(quarantine_toml("t::a"), quarantine_toml("t::b"), quarantine_toml("t::gone"))
    results = SuiteResults(
        (CaseResult("t::a", Outcome.FAILED), CaseResult("t::b", Outcome.EXPECTED_FAILURE))
    )
    found = reported(held, results)
    assert [(d.rule, d.severity, d.message, d.location) for d in found] == [
        (
            "tests.quarantined",
            Severity.WARNING,
            (
                "the integration suite of 'app': the quarantined test 't::a' failed; ignored until "
                "2999-01-01 (flaky in the test fixture)"
            ),
            None,
        ),
        (
            "tests.quarantined",
            Severity.WARNING,
            (
                "the integration suite of 'app': the quarantined test 't::b' was expected to fail; "
                "ignored until 2999-01-01 (flaky in the test fixture)"
            ),
            None,
        ),
        (
            "quarantine.unused",
            Severity.ERROR,
            "the integration suite of 'app' has no test 't::gone', which is quarantined",
            POLICY,
        ),
    ]


def test_quarantine_rules_cannot_be_excepted() -> None:
    assert [rule.id for rule in RULES] == [
        "tests.quarantined",
        "quarantine.expired",
        "quarantine.unused",
    ]
    assert {"quarantine.", "scenarios.", "tests."} <= set(FIXED_PREFIXES)
