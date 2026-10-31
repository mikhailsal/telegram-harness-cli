import pytest

from quality.diagnostics import Kind, Location, Rule, RuleRegistry, Severity


def rule(rule_id: str = "demo.rule", *, kind: Kind = Kind.VIOLATION) -> Rule:
    return Rule(
        id=rule_id,
        title="Demo",
        requirement="R01",
        explanation="Explains the demo.",
        remedy="Fix the demo.",
        kind=kind,
    )


@pytest.mark.parametrize("rule_id", ["Demo.rule", "demo", "demo..rule", "1demo.rule", "demo.rule."])
def test_rule_ids_must_be_dotted_lowercase(rule_id: str) -> None:
    with pytest.raises(ValueError, match="invalid rule id"):
        rule(rule_id)


def test_rule_diagnostic_carries_remedy_kind_and_location() -> None:
    usage = rule(kind=Kind.USAGE)
    diagnostic = usage.diagnostic("broken", location=Location("x.toml", 2), component="app")
    assert diagnostic.rule == "demo.rule"
    assert diagnostic.hint == "Fix the demo."
    assert diagnostic.kind is Kind.USAGE
    assert diagnostic.severity is Severity.ERROR
    assert diagnostic.component == "app"
    assert diagnostic.location == Location("x.toml", 2)


def test_rule_diagnostic_severity_can_be_overridden() -> None:
    assert rule().diagnostic("m", severity=Severity.WARNING).severity is Severity.WARNING


def test_registry_rejects_duplicate_ids() -> None:
    with pytest.raises(ValueError, match="duplicate rule id"):
        RuleRegistry([rule(), rule()])


def test_registry_lists_sorted_and_explains() -> None:
    registry = RuleRegistry([rule("b.rule"), rule("a.rule")])
    assert [r.id for r in registry] == ["a.rule", "b.rule"]
    assert registry.ids() == frozenset({"a.rule", "b.rule"})
    assert registry.get("missing.rule") is None
    assert registry.explain("missing.rule") is None
    text = registry.explain("a.rule")
    assert text is not None
    assert text.splitlines()[0] == "a.rule: Demo"
    assert "Requirement: R01" in text
    assert "Default severity: error" in text
    assert text.endswith("How to fix: Fix the demo.")


def test_rule_diagnostics_carry_read_only_data() -> None:
    rule = Rule(id="x.data", title="t", requirement="R04", explanation="e", remedy="r")
    found = rule.diagnostic("m", data={"symbol": "noqa: E501"})
    assert found.data == {"symbol": "noqa: E501"}
    assert rule.diagnostic("m").data == {}
