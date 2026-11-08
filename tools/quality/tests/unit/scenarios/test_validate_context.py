import pytest

from quality.diagnostics import Location
from quality.policy import Quarantine
from quality.scenarios.validate import (
    Subject,
    deleted_findings,
    manifest_findings,
    quarantine_findings,
)
from quality.testkit.policies import ComponentSpec, build_policy, quarantine_toml
from quality.testkit.scenarios import ScenarioSpec, manifest_subject, scenario_component

SMOKE = ScenarioSpec("smoke", ("e::smoke",), "cli", "e2e", requirements=("cli.smoke",))
FLOWS = ScenarioSpec("flows", ("i::flow",), "cli", "integration", requirements=("cli.flows",))


def findings(found: Subject) -> list[tuple[str, str]]:
    return [(d.rule, d.message) for d in manifest_findings(found, [])]


def quarantine(*entries: str) -> list[Quarantine]:
    spec = ComponentSpec(integration=("integration",), e2e=("e2e",))
    return build_policy(spec, extra="\n".join(entries)).quarantine


HTTP = ("http-api", "cli")
SUCCESS = ScenarioSpec(
    "list",
    ("i::list",),
    "http-api",
    "integration",
    requirements=("http-api.success", "http-api.contract"),
    operations=("listNotes",),
)
EXCUSED = {
    "http-api.invalid-input": "no input",
    "http-api.not-found": "no lookups",
    "http-api.permission": "public",
}


def test_every_openapi_operation_needs_a_scenario() -> None:
    owner = scenario_component(*HTTP)
    found = manifest_subject(
        owner,
        SMOKE,
        FLOWS,
        SUCCESS,
        operations=("listNotes", "GET /notes/{id}"),
        not_applicable=EXCUSED,
    )
    assert findings(found) == [
        ("scenarios.uncovered", "no scenario names the OpenAPI operation 'GET /notes/{id}'")
    ]


def test_named_operations_must_be_in_the_openapi_document() -> None:
    owner = scenario_component(*HTTP)
    found = manifest_subject(owner, SMOKE, FLOWS, SUCCESS, not_applicable=EXCUSED)
    assert findings(found) == [
        (
            "scenarios.reference",
            "scenario 'list' names operations, but the manifest names no OpenAPI document",
        )
    ]
    found = manifest_subject(owner, SMOKE, FLOWS, SUCCESS, operations=(), not_applicable=EXCUSED)
    assert findings(found) == [
        (
            "scenarios.reference",
            "scenario 'list' names 'listNotes', which is not an operation of the OpenAPI document",
        )
    ]


def test_an_openapi_document_needs_the_http_capability() -> None:
    found = manifest_subject(scenario_component(), SMOKE, FLOWS, operations=())
    assert findings(found) == [
        ("scenarios.reference", "'app' has an OpenAPI document but not 'http-api'")
    ]


def test_enabled_enhanced_profiles_need_tagged_scenarios_and_mutation_is_unavailable() -> None:
    owner = scenario_component(extra='enhanced = ["property", "performance", "mutation"]')
    tagged = ScenarioSpec("p", ("t::p",), "cli", "unit", enhanced="property")
    assert findings(manifest_subject(owner, SMOKE, FLOWS, tagged)) == [
        ("scenarios.uncovered", "'app' enables 'performance', but no scenario is tagged with it"),
        ("scenarios.enhanced", "'app' enables mutation testing, which has no tested profile"),
    ]


def test_quarantine_entries_may_suspend_only_scenarios_of_their_suite() -> None:
    held = quarantine(
        quarantine_toml("i::flow", "flows"),
        quarantine_toml("e::smoke", "flows", suite="e2e"),
        quarantine_toml("i::other", "missing"),
        quarantine_toml("x::y", "flows", component="app2"),
    )
    owner = scenario_component()
    found = manifest_findings(manifest_subject(owner, SMOKE, FLOWS), held)
    assert [(d.rule, d.message, d.location) for d in found] == [
        (
            "scenarios.reference",
            (
                "the quarantine of 'e::smoke' names 'flows', which is not a scenario of the e2e "
                "suite of 'app'"
            ),
            Location("quality.toml"),
        ),
        (
            "scenarios.reference",
            (
                "the quarantine of 'i::other' names 'missing', which is not a scenario of the "
                "integration suite of 'app'"
            ),
            Location("quality.toml"),
        ),
    ]
    assert [d.message for d in quarantine_findings(owner, {}, held[:1])] == [
        (
            "the quarantine of 'i::flow' names 'flows', which is not a scenario of the "
            "integration suite of 'app'"
        )
    ]


@pytest.mark.parametrize(
    ("previous", "deleted"),
    [((), []), (("smoke", "old"), []), (("smoke", "gone", "lost"), ["gone", "lost"])],
)
def test_a_scenario_of_the_base_revision_must_be_kept_or_retired(
    previous: tuple[str, ...], deleted: list[str]
) -> None:
    found = deleted_findings(
        manifest_subject(scenario_component(), SMOKE, retired={"old": "merged"}), previous
    )
    assert [(d.rule, d.message) for d in found] == [
        (
            "scenarios.deleted",
            f"scenario '{name}' of the base revision is neither in the manifest nor retired",
        )
        for name in deleted
    ]
