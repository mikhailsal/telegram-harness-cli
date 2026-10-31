from pathlib import Path

import pytest

from quality.policy import Capability, Profile, Role, load_policy, parse_policy
from quality.policy.load import MAX_POLICY_BYTES, key_path
from quality.testkit.policies import ComponentSpec, policy_toml


def rules_of(text: str) -> list[tuple[str, str]]:
    result = parse_policy(text.encode())
    assert result.policy is None
    return [(d.rule, d.message) for d in result.diagnostics]


def test_template_policy_is_valid() -> None:
    result = parse_policy(policy_toml().encode())
    assert result.diagnostics == ()
    assert result.policy is not None
    component = result.policy.components[0]
    assert (component.id, component.role, component.profile) == (
        "app",
        Role.APPLICATION,
        Profile.PYTHON,
    )
    assert component.capabilities == []
    assert component.no_boundary_reason == "pure computation without external boundaries"
    library = parse_policy(policy_toml(ComponentSpec(capabilities=("library",))).encode()).policy
    assert library is not None
    assert library.components[0].capabilities == [Capability.LIBRARY]
    assert result.policy.repository.default_branch == "main"
    assert result.policy.thresholds.parameters.error is None


def test_syntax_errors_report_line_and_column() -> None:
    result = parse_policy(b"schema_version = 1\n[repository\n")
    (diagnostic,) = result.diagnostics
    assert diagnostic.rule == "policy.syntax"
    assert diagnostic.location is not None
    assert (diagnostic.location.line, diagnostic.location.column) == (2, 12)


def test_non_utf8_and_oversized_files_are_syntax_errors() -> None:
    (non_utf8,) = parse_policy(b"\xff").diagnostics
    assert (non_utf8.rule, non_utf8.message[:28]) == (
        "policy.syntax",
        "policy file is not UTF-8: in",
    )
    (oversized,) = parse_policy(b"#" * (MAX_POLICY_BYTES + 1)).diagnostics
    assert oversized.message == "policy file exceeds 1 MiB"
    (deep,) = parse_policy(b"a = " + b"[" * 10_000 + b"]" * 10_000).diagnostics
    assert (deep.rule, deep.message) == ("policy.syntax", "policy file nests too deeply to parse")


def test_unknown_keys_are_reported_with_their_path() -> None:
    assert rules_of(policy_toml(ComponentSpec(extra='colour = "red"'))) == [
        ("policy.unknown-key", "unknown key 'components[0].colour'"),
    ]
    assert rules_of(policy_toml(extra="[surprise]\nx = 1")) == [
        ("policy.unknown-key", "unknown key 'surprise'"),
    ]


def test_empty_policy_reports_every_missing_section() -> None:
    keys = {message.split(":")[0] for _, message in rules_of("")}
    assert keys == {"schema_version", "repository", "thresholds", "coverage", "components"}


INVALID = [
    ("schema_version = 1", "schema_version = 2", "schema_version"),
    ('mode = "project"', 'mode = "strict"', "repository.mode"),
    ("line = 80", "line = 101", "coverage.line"),
    ("line = 80", 'line = "80"', "coverage.line"),
    ("line = 80", "line = 80.0", "coverage.line"),
    ("blob_bytes = 1048576", "blob_bytes = 0", "thresholds.blob_bytes"),
    ("blob_bytes = 1048576", "blob_bytes = true", "thresholds.blob_bytes"),
    (
        "new_file_must_be_covered = true",
        "new_file_must_be_covered = 1",
        "coverage.new_file_must_be_covered",
    ),
    ("parameters = { warning = 5 }", "parameters = {}", "thresholds.parameters"),
    (
        "nesting_depth = { warning = 3, error = 4 }",
        "nesting_depth = { warning = 4, error = 4 }",
        "thresholds.nesting_depth",
    ),
    ('id = "app"', 'id = "App"', "components[0].id"),
    ('id = "app"', 'id = "app-"', "components[0].id"),
    ('root = "."', 'root = "/abs"', "components[0].root"),
    ('root = "."', 'root = "a/../b"', "components[0].root"),
    ('root = "."', 'root = "a/"', "components[0].root"),
    ('root = "."', 'root = "a\\\\b"', "components[0].root"),
    ('source = ["src"]', 'source = ["src/*"]', "components[0].source[0]"),
    ('source = ["src"]', "source = []", "components[0].source"),
    ('source = ["src"]', 'source = ["./src"]', "components[0].source[0]"),
    ('profile = "python"', 'profile = "cobol"', "components[0].profile"),
    ('capabilities = ["library"]', 'capabilities = ["teleport"]', "components[0].capabilities[0]"),
    ('capabilities = ["library"]', "capabilities = []", "components[0]"),
    (
        'capabilities = ["library"]',
        'capabilities = ["library"]\nno_boundary_reason = "x"',
        "components[0]",
    ),
    ('capabilities = ["library"]', 'no_boundary_reason = "  "', "components[0].no_boundary_reason"),
]


@pytest.mark.parametrize(("old", "new", "key"), INVALID)
def test_invalid_values_are_reported_with_their_key(old: str, new: str, key: str) -> None:
    text = policy_toml(ComponentSpec(capabilities=("library",)))
    assert old in text
    found = rules_of(text.replace(old, new, 1))
    assert found
    assert all(rule == "policy.invalid" for rule, _ in found)
    assert any(message.startswith(key) for _, message in found), found


EXCEPTION = """
[[exceptions]]
rule = "policy.invalid"
path = "src/a.py"
reason = "demonstration"
owner = "team"
"""


@pytest.mark.parametrize(
    ("addition", "key"),
    [
        ("review = 2027-01-01", None),
        ("expires = 2027-01-01", None),
        ("", "exceptions[0]"),
        ('expires = "2027-01-01"', "exceptions[0].expires"),
        ('expires = 2027-01-01\nsymbol = ""', "exceptions[0].symbol"),
    ],
)
def test_exceptions_need_dates_and_strict_types(addition: str, key: str | None) -> None:
    result = parse_policy(policy_toml(extra=EXCEPTION + addition).encode())
    if key is None:
        assert result.diagnostics == ()
        return
    assert [d.message.split(":")[0] for d in result.diagnostics] == [key]


@pytest.mark.parametrize(
    ("pattern", "valid"),
    [("generated/**", True), ("src/*.pb.py", True), ("**/*.py", False), ("*.md", False)],
)
def test_classification_globs_must_start_literally(pattern: str, valid: bool) -> None:
    extra = (
        f'[[classification]]\npattern = "{pattern}"\ncategory = "generated"\nreason = "codegen"\n'
    )
    result = parse_policy(policy_toml(extra=extra).encode())
    assert (result.diagnostics == ()) is valid


def test_architecture_edges_use_from_and_to() -> None:
    architecture = """
[[components.architecture.modules]]
name = "domain"
paths = ["src/domain/**"]

[[components.architecture.modules]]
name = "web"
paths = ["src/web/**"]

[[components.architecture.forbidden]]
from = "domain"
to = "web"
"""
    result = parse_policy((policy_toml() + architecture).encode())
    assert result.policy is not None
    (edge,) = result.policy.components[0].architecture.forbidden
    assert (edge.source, edge.target) == ("domain", "web")


def test_key_path_rendering() -> None:
    assert key_path(()) == "<root>"
    assert key_path(("components", 0, "tests", "unit", 2)) == "components[0].tests.unit[2]"


def test_load_policy_reads_the_root_file(tmp_path: Path) -> None:
    (tmp_path / "quality.toml").write_text(policy_toml())
    assert load_policy(tmp_path).policy is not None


def test_load_policy_reports_a_missing_file(tmp_path: Path) -> None:
    (diagnostic,) = load_policy(tmp_path).diagnostics
    assert diagnostic.rule == "policy.missing"


def test_load_policy_bounds_the_read(tmp_path: Path) -> None:
    (tmp_path / "quality.toml").write_bytes(b"#" * (MAX_POLICY_BYTES * 3))
    (diagnostic,) = load_policy(tmp_path).diagnostics
    assert diagnostic.message == "policy file exceeds 1 MiB"


def test_components_without_boundaries_must_say_why() -> None:
    result = parse_policy(policy_toml(ComponentSpec(capabilities=())).encode())
    assert result.policy is not None
    component = result.policy.components[0]
    assert component.capabilities == []
    assert component.no_boundary_reason == "pure computation without external boundaries"


def test_exception_rule_ids_must_be_dotted() -> None:
    text = policy_toml(
        extra=EXCEPTION.replace('"policy.invalid"', '"Invalid"') + "review = 2027-01-01"
    )
    assert rules_of(text) == [
        (
            "policy.invalid",
            "exceptions[0].rule: Value error, must be a dotted rule ID such as 'coverage.line'",
        ),
    ]
