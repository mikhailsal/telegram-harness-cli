from collections.abc import Callable

import pytest

from quality.diagnostics import Location, Severity
from quality.policy import Quarantine, Suite
from quality.policy.scenarios import Scenario
from quality.scenarios.reconcile import Ran, ScenarioSummary, reconcile, selected
from quality.testing import ACCESSIBILITY_SCAN, CaseResult, Outcome, SuiteResults
from quality.testkit.policies import ComponentSpec, build_policy, quarantine_toml
from quality.testkit.scenarios import ScenarioSpec, build_manifest

PATH = "scenarios.toml"
PASSED = Outcome.PASSED
UNIT = ScenarioSpec("unit", ("u::a", "u::b*"))
FLOW = ScenarioSpec("flow", ("i::flow",), "cli", "integration")
LATER = ScenarioSpec("later", ("e::smoke",), "cli", "e2e", stage="merge")


def results(*cases: tuple[str, Outcome]) -> SuiteResults:
    return SuiteResults(tuple(CaseResult(name, outcome) for name, outcome in cases))


def every(_scenario: Scenario) -> bool:
    return True


def committed(scenario: Scenario) -> bool:
    return scenario.stage == "commit"


def held(*entries: str) -> list[Quarantine]:
    spec = ComponentSpec(integration=("i",), e2e=("e",))
    return build_policy(spec, extra="\n".join(entries)).quarantine


def check(
    ran: Ran,
    *specs: ScenarioSpec,
    entries: list[Quarantine] | None = None,
    required: Callable[[Scenario], bool] = every,
) -> tuple[list[tuple[str, Severity, str]], ScenarioSummary]:
    manifest = build_manifest(*(specs or (UNIT, FLOW)))
    found, summary = reconcile(manifest, PATH, required, ran, entries or [])
    assert {(d.location, d.component) for d in found} <= {(Location(PATH), "app")}
    return [(d.rule, d.severity, d.message) for d in found], summary


GOOD: Ran = {
    Suite.UNIT: results(("u::a", PASSED), ("u::b1", PASSED), ("u::b2", PASSED), ("x", PASSED)),
    Suite.INTEGRATION: results(("i::flow", PASSED)),
}


def test_a_selector_is_an_exact_name_or_a_prefix() -> None:
    names = ["a::b", "a::bc", "a::c"]
    assert selected("a::b", names) == ["a::b"]
    assert selected("a::b*", names) == ["a::b", "a::bc"]
    assert selected("a::", names) == []


def test_scenarios_whose_selected_cases_passed_are_proved() -> None:
    assert check(GOOD) == ([], ScenarioSummary("app", 2, 2))


def test_a_scenario_is_unverified_when_its_suite_left_no_results() -> None:
    message = (
        "scenario 'flow' (cli, integration): the integration suite left no results to prove it"
    )
    for ran in ({Suite.UNIT: GOOD[Suite.UNIT]}, {**GOOD, Suite.INTEGRATION: None}):
        assert check(ran) == (
            [("scenarios.unverified", Severity.ERROR, message)],
            ScenarioSummary("app", 2, 1),
        )


def test_scenarios_not_required_now_are_deferred() -> None:
    assert check(GOOD, UNIT, FLOW, LATER, required=committed) == (
        [],
        ScenarioSummary("app", 2, 2, deferred=1),
    )


def test_a_missing_case_is_absent_and_similar_names_are_suggested() -> None:
    ran: Ran = {Suite.UNIT: results(("u::aa", PASSED), ("u::c", PASSED))}
    found, summary = check(ran, UNIT)
    assert found == [
        (
            "scenarios.absent",
            Severity.ERROR,
            (
                "scenario 'unit' (library, unit): the unit suite has no case 'u::a'; similar: "
                "'u::aa', 'u::c'; 'u::b*' selects no case of the unit suite"
            ),
        )
    ]
    assert summary == ScenarioSummary("app", 1, 0)
    found, _ = check({Suite.UNIT: results(("zzz", PASSED))}, ScenarioSpec(tests=("u::a",)))
    assert found[0][2].endswith("the unit suite has no case 'u::a'")


def test_an_exact_name_that_selects_several_cases_is_ambiguous() -> None:
    # pytest reports a teardown error as a second case of the same name.
    ran: Ran = {Suite.UNIT: results(("u::a", PASSED), ("u::a", PASSED), ("u::b", PASSED))}
    found, _ = check(ran, UNIT)
    assert found == [
        (
            "scenarios.ambiguous",
            Severity.ERROR,
            "scenario 'unit' (library, unit): 'u::a' names 2 cases of the unit suite",
        )
    ]


@pytest.mark.parametrize(
    ("outcome", "rule", "verb"),
    [
        (Outcome.FAILED, "scenarios.failed", "failed"),
        (Outcome.ERROR, "scenarios.failed", "raised an error"),
        (Outcome.SKIPPED, "scenarios.skipped", "was skipped"),
        (Outcome.EXPECTED_FAILURE, "scenarios.skipped", "was expected to fail"),
    ],
)
def test_a_selected_case_must_pass(outcome: Outcome, rule: str, verb: str) -> None:
    ran: Ran = {Suite.UNIT: results(("u::a", PASSED), ("u::b1", outcome), ("u::b2", outcome))}
    found, summary = check(ran, UNIT)
    message = f"scenario 'unit' (library, unit): 'u::b1' {verb}; 'u::b2' {verb}"
    assert found == [(rule, Severity.ERROR, message)]
    assert summary.passed == 0


def test_each_problem_kind_is_reported_once_per_scenario() -> None:
    spec = ScenarioSpec(tests=("u::a", "u::a*", "u::b", "u::c"))
    ran: Ran = {Suite.UNIT: results(("u::a", Outcome.FAILED), ("u::b", Outcome.SKIPPED))}
    found, _ = check(ran, spec)
    assert [(rule, message.split(": ", 1)[1]) for rule, _, message in found] == [
        ("scenarios.failed", "'u::a' failed"),
        ("scenarios.skipped", "'u::b' was skipped"),
        ("scenarios.absent", "the unit suite has no case 'u::c'; similar: 'u::b', 'u::a'"),
    ]


def test_a_quarantined_case_suspends_only_the_scenarios_its_entry_names() -> None:
    ran: Ran = {**GOOD, Suite.INTEGRATION: results(("i::flow", Outcome.FAILED))}
    entries = held(quarantine_toml("i::flow", "flow", expires="2999-02-03"))
    found, summary = check(ran, entries=entries)
    message = (
        "scenario 'flow' (cli, integration) is suspended: 'i::flow' is quarantined until 2999-02-03"
    )
    assert found == [("scenarios.suspended", Severity.WARNING, message)]
    assert summary == ScenarioSummary("app", 2, 1, suspended=1)
    found, summary = check(ran, entries=held(quarantine_toml("i::flow")))
    message = (
        "scenario 'flow' (cli, integration): 'i::flow' is quarantined, and its entry does not "
        "name this scenario"
    )
    assert found == [("scenarios.quarantined", Severity.ERROR, message)]
    assert summary == ScenarioSummary("app", 2, 1)


def test_a_suspension_does_not_hide_other_problems() -> None:
    spec = ScenarioSpec("flow", ("i::flow", "i::other"), "cli", "integration")
    ran: Ran = {Suite.INTEGRATION: results(("i::flow", PASSED), ("i::other", Outcome.FAILED))}
    found, summary = check(ran, spec, entries=held(quarantine_toml("i::flow", "flow")))
    assert [rule for rule, _, _ in found] == ["scenarios.failed"]
    assert summary == ScenarioSummary("app", 1, 0)


def test_quarantine_entries_of_other_suites_and_components_do_not_apply() -> None:
    entries = held(
        quarantine_toml("i::flow", "flow", suite="e2e"),
        quarantine_toml("i::flow", "flow", component="other"),
    )
    assert check(GOOD, entries=entries) == ([], ScenarioSummary("app", 2, 2))


SCANNED = ScenarioSpec(
    "scanned",
    ("e::form*",),
    "ui",
    "e2e",
    requirements=("ui.flows", "ui.accessibility", "ui.keyboard"),
    boundary="a routed API",
)
STATIC = ScenarioSpec("static", ("e::home",), "ui", "e2e", boundary="the static build")
AGAIN = ScenarioSpec("again", ("e::home",), "ui", "e2e", boundary="the static build")


def test_a_scanned_requirement_needs_the_evidence_of_every_selected_case() -> None:
    scan = frozenset({ACCESSIBILITY_SCAN})
    cases = (
        CaseResult("e::form-narrow", PASSED, evidence=scan),
        CaseResult("e::form-wide", PASSED),
        CaseResult("e::home", PASSED),
    )
    found, summary = check({Suite.E2E: SuiteResults(cases)}, SCANNED, STATIC, AGAIN)
    assert found == [
        (
            "scenarios.evidence",
            Severity.ERROR,
            (
                "scenario 'scanned' (ui, e2e): 'e::form-wide' passed without a valid "
                "accessibility scan in every viewport"
            ),
        )
    ]
    assert summary == ScenarioSummary("app", 3, 2, boundaries=("a routed API", "the static build"))
    proved = SuiteResults((CaseResult("e::form", PASSED, evidence=scan),))
    assert check({Suite.E2E: proved}, SCANNED)[0] == []
