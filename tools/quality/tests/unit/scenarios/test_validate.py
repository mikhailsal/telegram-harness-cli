import pytest

from quality.diagnostics import Location
from quality.scenarios.manifest import parse_manifest
from quality.scenarios.validate import Subject, manifest_findings
from quality.testkit.scenarios import (
    MANIFEST_PATH,
    ScenarioSpec,
    build_manifest,
    manifest_subject,
    manifest_toml,
    scenario_component,
)

SMOKE = ScenarioSpec("smoke", ("e::smoke",), "cli", "e2e", requirements=("cli.smoke",))
FLOWS = ScenarioSpec("flows", ("i::flow",), "cli", "integration", requirements=("cli.flows",))
API = ScenarioSpec("api", ("t::api",), "library", requirements=("library.public-api",))

FORBIDDEN = ScenarioSpec(
    "forbidden",
    ("i::f",),
    "authorization",
    "integration",
    requirements=("authorization.forbidden",),
)
ISOLATION = (
    "scenarios.uncovered",
    (
        "no scenario proves 'authorization.isolation': tenants cannot reach each other, "
        "or declare it not applicable with a reason"
    ),
)


def findings(found: Subject) -> list[tuple[str, str]]:
    return [(d.rule, d.message) for d in manifest_findings(found, [])]


def test_a_complete_manifest_has_no_findings() -> None:
    assert findings(manifest_subject(scenario_component(), SMOKE, FLOWS)) == []


def test_findings_are_located_at_the_manifest() -> None:
    found = manifest_findings(manifest_subject(scenario_component()), [])
    assert found
    assert {(d.location, d.component) for d in found} == {(Location(MANIFEST_PATH), "app")}


def test_scenario_ids_are_unique_across_active_and_retired() -> None:
    text = manifest_toml(SMOKE, SMOKE, FLOWS, retired={"flows": "replaced", "old": "gone"})
    text += '[[retired]]\nid = "old"\nreason = "again"\n'
    loaded = parse_manifest(text.encode(), MANIFEST_PATH, "app")
    assert loaded.manifest is not None
    assert findings(Subject(scenario_component(), loaded.manifest, MANIFEST_PATH)) == [
        ("scenarios.ambiguous", "scenario 'smoke' is declared more than once"),
        ("scenarios.ambiguous", "scenario 'old' is retired more than once"),
        ("scenarios.ambiguous", "scenario 'flows' is both active and retired"),
    ]


def test_a_requirement_must_exist_belong_to_the_capability_and_fit_the_level() -> None:
    found = manifest_subject(
        scenario_component(),
        SMOKE,
        FLOWS,
        ScenarioSpec("a", ("e::a",), "cli", "e2e", requirements=("cli.unknown",)),
        ScenarioSpec("b", ("e::b",), "cli", "e2e", requirements=("ui.smoke",)),
        ScenarioSpec("c", ("t::c",), "cli", "unit", requirements=("cli.smoke",)),
    )
    assert findings(found) == [
        ("scenarios.reference", "scenario 'a' names the unknown requirement 'cli.unknown'"),
        (
            "scenarios.reference",
            "scenario 'b' proves 'cli', but 'ui.smoke' is a requirement of 'ui'",
        ),
        (
            "scenarios.level",
            "scenario 'c' proves 'cli.smoke' with the unit suite; it accepts e2e, acceptance",
        ),
    ]


UI = ("ui.smoke", "ui.flows", "ui.accessibility", "ui.keyboard")


def ui(identifier: str, *requirements: str, boundary: str | None = "a routed API") -> ScenarioSpec:
    return ScenarioSpec(
        identifier, (f"e::{identifier}",), "ui", "e2e", requirements=requirements, boundary=boundary
    )


def test_a_browser_component_proves_accessibility_and_keyboard_use() -> None:
    owner = scenario_component("ui", browser=True)
    assert findings(manifest_subject(owner, ui("all", *UI))) == []
    assert findings(manifest_subject(owner, ui("some", *UI[:2]))) == [
        (
            "scenarios.uncovered",
            (
                "no scenario proves 'ui.accessibility': accessibility scans of the declared "
                "rendered states"
            ),
        ),
        (
            "scenarios.uncovered",
            (
                "no scenario proves 'ui.keyboard': keyboard operation and focus handling of the "
                "critical controls"
            ),
        ),
    ]


def test_every_scenario_of_a_browser_component_names_its_tested_boundary() -> None:
    owner = scenario_component("ui", browser=True)
    found = manifest_subject(owner, ui("all", *UI), ui("bare", boundary=None))
    assert findings(found) == [
        (
            "scenarios.reference",
            "scenario 'bare' names no tested boundary; every scenario of a browser component does",
        )
    ]


def test_only_a_browser_component_proves_the_browser_requirements() -> None:
    found = manifest_subject(scenario_component("ui"), ui("ui", *UI))
    assert findings(found) == [
        (
            "scenarios.reference",
            f"scenario 'ui' proves '{identifier}', which only a browser component proves",
        )
        for identifier in UI[2:]
    ]


def test_a_scenario_must_fit_its_component() -> None:
    owner = scenario_component(extra='enhanced = ["performance"]')
    found = manifest_subject(
        owner,
        SMOKE,
        FLOWS,
        ScenarioSpec("a", ("t::a", "t::a"), "library", "contract", enhanced="property"),
        ScenarioSpec("b", ("t::b",), "cli", "unit", enhanced="performance"),
    )
    assert findings(found) == [
        ("scenarios.reference", "scenario 'a' proves 'library', which 'app' does not declare"),
        (
            "scenarios.reference",
            "scenario 'a' is proved by the contract suite; 'app' has none",
        ),
        (
            "scenarios.reference",
            "scenario 'a' is tagged 'property', which 'app' does not enable",
        ),
        ("scenarios.ambiguous", "scenario 'a' names the test 't::a' more than once"),
        ("scenarios.level", "scenario 'b' measures performance in the unit suite"),
    ]


def test_every_obligation_of_a_capability_needs_a_scenario() -> None:
    assert findings(manifest_subject(scenario_component("cli", "library"))) == [
        ("scenarios.uncovered", "'app' declares 'cli', but no scenario proves it"),
        (
            "scenarios.uncovered",
            "no scenario proves 'cli.smoke': the built artifact starts and answers",
        ),
        (
            "scenarios.uncovered",
            "no scenario proves 'cli.flows': declared critical flows through the command line",
        ),
        ("scenarios.uncovered", "'app' declares 'library', but no scenario proves it"),
        (
            "scenarios.uncovered",
            "no scenario proves 'library.public-api': consumer code uses the public API",
        ),
    ]


def test_only_optional_requirements_may_be_declared_not_applicable() -> None:
    owner = scenario_component("authorization", "cli")
    found = manifest_subject(
        owner,
        SMOKE,
        FLOWS,
        FORBIDDEN,
        not_applicable={"cli.smoke": "no artifact", "authorization.forbidden": "open"},
    )
    assert findings(found) == [
        (
            "scenarios.ambiguous",
            "'authorization.forbidden' is both proved by a scenario and declared not applicable",
        ),
        ISOLATION,
        (
            "scenarios.ambiguous",
            "'cli.smoke' is both proved by a scenario and declared not applicable",
        ),
    ]
    found = manifest_subject(
        owner, SMOKE, FLOWS, not_applicable={"authorization.forbidden": "open"}
    )
    assert findings(found) == [
        ("scenarios.uncovered", "'app' declares 'authorization', but no scenario proves it"),
        (
            "scenarios.uncovered",
            (
                "'authorization.forbidden' (forbidden access is refused) always applies; "
                "it cannot be not applicable"
            ),
        ),
        ISOLATION,
    ]


def test_an_excused_requirement_must_be_one_of_a_declared_capability() -> None:
    owner = scenario_component("authorization", "cli")
    excused = {"authorization.isolation": "single tenant", "ui.smoke": "x", "cli.nothing": "y"}
    found = manifest_subject(owner, SMOKE, FLOWS, FORBIDDEN, not_applicable=excused)
    assert findings(found) == [
        ("scenarios.reference", "'ui.smoke' is not a requirement of a capability 'app' declares"),
        (
            "scenarios.reference",
            "'cli.nothing' is not a requirement of a capability 'app' declares",
        ),
    ]


def test_a_requirement_is_declared_not_applicable_once() -> None:
    owner = scenario_component("authorization", "cli")
    text = manifest_toml(SMOKE, FLOWS, FORBIDDEN, not_applicable={"authorization.isolation": "x"})
    entry = '[[not_applicable]]\nrequirement = "authorization.isolation"\nreason = "again"\n'
    loaded = parse_manifest((text + entry).encode(), MANIFEST_PATH, "app")
    assert loaded.manifest is not None
    found = findings(Subject(owner, loaded.manifest, MANIFEST_PATH))
    assert found == [
        (
            "scenarios.ambiguous",
            "'authorization.isolation' is declared not applicable more than once",
        )
    ]


def test_the_builder_parses_its_manifest() -> None:
    assert [scenario.id for scenario in build_manifest(API).scenarios] == ["api"]
    with pytest.raises(AssertionError, match="invalid test manifest"):
        build_manifest(component="Bad Name")
