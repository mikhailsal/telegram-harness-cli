import json

from quality.policy.schema import SCHEMAS, policy_schema_text, scenarios_schema_text


def test_schema_is_deterministic_and_closed() -> None:
    text = policy_schema_text()
    assert text == policy_schema_text()
    assert text.endswith("}\n")
    schema = json.loads(text)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["title"] == "quality.toml"
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {
        "schema_version",
        "repository",
        "thresholds",
        "coverage",
        "components",
    }
    for definition in schema["$defs"].values():
        if definition.get("type") == "object":
            assert definition["additionalProperties"] is False


def test_schema_uses_toml_spelling_of_aliased_keys() -> None:
    edge = json.loads(policy_schema_text())["$defs"]["Edge"]
    assert set(edge["properties"]) == {"from", "to"}


def test_the_scenario_manifest_schema_is_closed_and_listed() -> None:
    text = scenarios_schema_text()
    assert text == scenarios_schema_text()
    schema = json.loads(text)
    assert schema["title"] == "scenario manifest"
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"schema_version", "component"}
    assert set(schema["$defs"]["Scenario"]["required"]) == {
        "id",
        "summary",
        "capability",
        "level",
        "stage",
        "tests",
    }
    assert {path: render() for path, render in SCHEMAS.items()} == {
        "docs/reference/policy.schema.json": policy_schema_text(),
        "docs/reference/scenarios.schema.json": text,
    }
