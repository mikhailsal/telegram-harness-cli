import pytest

from quality.catalog import build_registry
from quality.policy import Policy, cross_validate, owned_paths, parse_policy
from quality.testkit.policies import ComponentSpec, policy_toml, quarantine_toml

KNOWN = build_registry().ids()


def policy_of(text: str) -> Policy:
    result = parse_policy(text.encode())
    assert result.diagnostics == ()
    assert result.policy is not None
    return result.policy


def problems(*components: ComponentSpec, extra: str = "") -> list[tuple[str, str]]:
    policy = policy_of(policy_toml(*components, extra=extra))
    return [(d.rule, d.message) for d in cross_validate(policy, KNOWN)]


def test_template_policy_has_no_conflicts() -> None:
    assert problems() == []


def test_owned_paths_are_prefixed_by_the_component_root() -> None:
    root = policy_of(policy_toml()).components[0]
    nested = policy_of(policy_toml(ComponentSpec(root="apps/web"))).components[0]
    assert owned_paths(root) == ["src", "tests"]
    assert owned_paths(nested) == ["apps/web/src", "apps/web/tests"]


def test_duplicate_ids_and_roots_conflict() -> None:
    found = problems(ComponentSpec(root="a"), ComponentSpec(root="a"))
    assert ("policy.conflict", "duplicate component id 'app'") in found
    assert ("policy.conflict", "several components share the root 'a'") in found


def test_nested_component_paths_must_not_overlap() -> None:
    found = problems(
        ComponentSpec(id="outer", source=("packages",)),
        ComponentSpec(id="inner", root="packages/lib"),
    )
    assert found == [
        ("policy.conflict", "'packages' of 'outer' overlaps 'packages/lib/src' of 'inner'"),
        ("policy.conflict", "'packages' of 'outer' overlaps 'packages/lib/tests' of 'inner'"),
    ]


def test_siblings_with_a_common_prefix_do_not_overlap() -> None:
    assert problems(ComponentSpec(id="a", root="app"), ComponentSpec(id="b", root="app2")) == []


def test_source_and_test_roots_must_be_disjoint_within_a_component() -> None:
    assert problems(ComponentSpec(source=("src",), unit=("src",))) == [
        ("policy.conflict", "'src' is declared more than once"),
    ]
    assert problems(ComponentSpec(source=("src",), unit=("src/tests",))) == [
        ("policy.conflict", "source root 'src' overlaps test root 'src/tests'"),
    ]


def test_dependencies_on_declared_components_are_accepted() -> None:
    app = ComponentSpec(id="app", root="app", extra='depends_on = ["lib"]')
    assert problems(app, ComponentSpec(id="lib", root="lib")) == []


def test_dependencies_must_exist_and_not_be_self() -> None:
    assert problems(ComponentSpec(extra='depends_on = ["ghost", "app"]')) == [
        ("policy.reference", "depends on undeclared component 'ghost'"),
        ("policy.conflict", "component depends on itself"),
    ]


ARCHITECTURE = """
[[components.architecture.modules]]
name = "domain"
paths = ["src/domain/**"]

[[components.architecture.modules]]
name = "domain"
paths = ["src/core/**"]

[[components.architecture.forbidden]]
from = "domain"
to = "web"

[[components.architecture.forbidden]]
from = "domain"
to = "domain"
"""


def test_architecture_modules_and_edges_are_checked() -> None:
    assert problems(extra=ARCHITECTURE) == [
        ("policy.conflict", "module 'domain' is declared more than once"),
        ("policy.reference", "forbidden edge refers to undeclared module 'web'"),
        ("policy.conflict", "forbidden edge from 'domain' to itself"),
    ]


LAYERS = """
[components.architecture]
layers = ["web", "domain", "web"]

[[components.architecture.modules]]
name = "domain"
paths = ["src/domain/**"]
"""


def test_layers_must_name_declared_modules_once() -> None:
    assert problems(extra=LAYERS) == [
        ("policy.conflict", "layer 'web' is listed more than once"),
        ("policy.reference", "layers refer to undeclared module 'web'"),
        ("policy.reference", "layers refer to undeclared module 'web'"),
    ]


def exception(rule: str, symbol: str = "") -> str:
    lines = [
        "[[exceptions]]",
        f'rule = "{rule}"',
        'path = "src/a.py"',
        'reason = "demo"',
        'owner = "team"',
        "review = 2027-01-01",
    ]
    if symbol:
        lines.append(f'symbol = "{symbol}"')
    return "\n".join(lines) + "\n"


@pytest.mark.parametrize("symbol", ["", "run"])
def test_duplicate_exceptions_conflict(symbol: str) -> None:
    extra = exception("policy.invalid", symbol) * 2 + exception("policy.invalid", "other")
    expected = f"duplicate exception for 'policy.invalid' at 'src/a.py' {symbol}".rstrip()
    assert problems(extra=extra) == [("policy.conflict", expected)]


def test_exceptions_must_name_known_rules() -> None:
    assert problems(extra=exception("no.such-rule")) == [
        ("policy.reference", "exception refers to unknown rule 'no.such-rule'"),
    ]


def test_classification_patterns_must_be_unique() -> None:
    entry = '[[classification]]\npattern = "gen/**"\ncategory = "generated"\nreason = "codegen"\n'
    assert problems(extra=entry * 2) == [
        ("policy.conflict", "classification pattern 'gen/**' is declared twice"),
    ]


@pytest.mark.parametrize("category", ["source", "tests"])
def test_classification_cannot_assign_source_or_tests(category: str) -> None:
    entry = f'[[classification]]\npattern = "x/**"\ncategory = "{category}"\nreason = "move"\n'
    message = (
        f"classification pattern 'x/**' assigns '{category}'; "
        "source and test files are declared by component roots"
    )
    assert problems(extra=entry) == [("policy.conflict", message)]


def test_quarantine_entries_must_be_unique_and_name_a_declared_suite() -> None:
    spec = ComponentSpec(integration=("integration",))
    entries = [
        quarantine_toml("a::b"),
        quarantine_toml("a::b"),
        quarantine_toml("a::c", component="ghost"),
        quarantine_toml("a::d", suite="unit"),
        quarantine_toml("a::e", suite="e2e"),
        quarantine_toml("a::b", suite="e2e", component="ghost"),
    ]
    assert problems(spec, extra="\n".join(entries)) == [
        ("policy.conflict", "'a::b' of the integration suite is quarantined more than once"),
        ("policy.reference", "quarantine refers to undeclared component 'ghost'"),
        ("policy.conflict", "'a::d' is a unit test; unit tests cannot be quarantined"),
        ("policy.reference", "quarantine refers to the e2e suite, which is not declared"),
        ("policy.reference", "quarantine refers to undeclared component 'ghost'"),
    ]


def test_a_valid_quarantine_entry_has_no_conflicts() -> None:
    spec = ComponentSpec(integration=("integration",))
    assert problems(spec, extra=quarantine_toml("a::b", "flow")) == []
