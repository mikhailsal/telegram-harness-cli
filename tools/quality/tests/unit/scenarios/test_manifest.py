import pytest

from quality.diagnostics import Kind, Location
from quality.policy import Suite
from quality.policy.scenarios import Stage
from quality.scenarios.manifest import MAX_MANIFEST_BYTES, parse_manifest
from quality.testkit.scenarios import ScenarioSpec, manifest_toml

PATH = "app/scenarios.toml"


def problems(text: str | bytes, component: str = "app") -> list[tuple[str, str, int | None]]:
    data = text.encode() if isinstance(text, str) else text
    loaded = parse_manifest(data, PATH, component)
    assert loaded.manifest is None
    found = loaded.diagnostics
    assert {(d.rule, d.kind, d.component) for d in found} == {
        ("scenarios.manifest", Kind.USAGE, component)
    }
    return [
        (d.message, d.location.path if d.location else "", d.location and d.location.line)
        for d in found
    ]


def test_a_valid_manifest_is_parsed() -> None:
    text = manifest_toml(
        ScenarioSpec(tests=("a::b", "a::c*"), requirements=("library.public-api",)),
        not_applicable={"http-api.not-found": "no lookups"},
        retired={"old": "replaced"},
        openapi="openapi.json",
    )
    loaded = parse_manifest(text.encode(), PATH, "app")
    assert loaded.diagnostics == ()
    manifest = loaded.manifest
    assert manifest is not None
    (scenario,) = manifest.scenarios
    assert (scenario.level, scenario.stage, scenario.tests) == (
        Suite.UNIT,
        Stage.COMMIT,
        ["a::b", "a::c*"],
    )
    assert manifest.openapi == "openapi.json"
    assert [entry.requirement for entry in manifest.not_applicable] == ["http-api.not-found"]
    assert [entry.id for entry in manifest.retired] == ["old"]


def test_an_oversized_or_undecodable_manifest_is_rejected() -> None:
    assert problems(b" " * (MAX_MANIFEST_BYTES + 1)) == [
        ("the scenario manifest exceeds 1 MiB", PATH, None)
    ]
    assert problems(b"\xff") == [
        ("the scenario manifest is not UTF-8: invalid start byte", PATH, None)
    ]


def test_a_toml_error_names_its_line() -> None:
    assert problems("schema_version = 1\ncomponent = \n") == [("Invalid value", PATH, 2)]


def test_a_manifest_that_nests_too_deeply_is_rejected() -> None:
    assert problems("a = " + "[" * 10_000 + "]" * 10_000) == [
        ("the scenario manifest nests too deeply to parse", PATH, None)
    ]


@pytest.mark.parametrize(
    ("selector", "valid"),
    [("a::b", True), ("a::*", True), ("*", False), ("a*b", False), ("a**", False)],
)
def test_a_selector_is_a_name_or_a_prefix_with_one_trailing_star(
    selector: str, valid: bool
) -> None:
    text = manifest_toml(ScenarioSpec(tests=(selector,)))
    loaded = parse_manifest(text.encode(), PATH, "app")
    assert (loaded.manifest is not None) is valid
    if not valid:
        message = (
            "scenarios[0].tests[0]: Value error, must be a test case name, or a prefix of one "
            "followed by a single trailing '*'"
        )
        assert [d.message for d in loaded.diagnostics] == [message]


def test_schema_errors_name_their_keys() -> None:
    scenario = 'id = "x"\nsummary = "s"\ncapability = "cli"\nlevel = "unit"\ntests = []\n'
    text = f'schema_version = 1\ncomponent = "app"\n[[scenarios]]\n{scenario}'
    assert [message for message, _, _ in problems(text)] == [
        "scenarios[0].stage: Field required",
        "scenarios[0].tests: List should have at least 1 item after validation, not 0",
    ]


def test_scenario_ids_are_lowercase_names() -> None:
    assert [message for message, _, _ in problems(manifest_toml(ScenarioSpec(id="Smoke")))] == [
        (
            "scenarios[0].id: Value error, must be lowercase words joined by '.' or '-', "
            "starting with a letter"
        )
    ]


def test_a_manifest_must_name_its_own_component() -> None:
    assert problems(manifest_toml(component="other")) == [
        ("the manifest is for 'other', not for 'app'", PATH, None)
    ]


def test_the_location_is_the_manifest() -> None:
    loaded = parse_manifest(b"x", PATH, "app")
    assert loaded.diagnostics[0].location == Location(PATH, 1)
