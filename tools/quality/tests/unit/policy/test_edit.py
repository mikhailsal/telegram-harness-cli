import dataclasses

import pytest

from quality.policy.edit import Draft, EditError, add_component, component_toml
from quality.testkit.policies import ComponentSpec, policy_toml

WEB = Draft("web", "typescript", "web", ("src",), ("tests",), (), "a pure library")
TRAILER = """
# The CI wrapper contract.
[ci]
workflow = ".github/workflows/quality.yml"
required_jobs = ["gate"]
"""
ARCHITECTURE = """
[components.architecture]
layers = ["api", "core"]
modules = [
    { name = "api", paths = ["src/app/api/**"] },
    { name = "core", paths = ["src/app/core/**"] },
]
"""


def test_a_component_renders_as_one_table() -> None:
    assert component_toml(WEB).splitlines() == [
        "[[components]]",
        'id = "web"',
        'role = "application"',
        'profile = "typescript"',
        'root = "web"',
        'source = ["src"]',
        'tests = { unit = ["tests"] }',
        "capabilities = []",
        'no_boundary_reason = "a pure library"',
    ]
    quoted = Draft("calc", "dart", "calc", ("lib",), ("test",), (), 'pure "math"')
    assert component_toml(quoted).splitlines()[-1] == 'no_boundary_reason = "pure \\"math\\""'
    api = Draft("api", "python", "api", ("src",), ("tests",), ("cli", "http"))
    assert component_toml(api).splitlines()[-1] == 'capabilities = ["cli", "http"]'


def test_a_target_follows_the_profile() -> None:
    browser = Draft("ui", "typescript", "ui", ("src",), ("tests",), ("ui",), None, "browser")
    browser = dataclasses.replace(browser, framework="react", bundler="vite")
    assert component_toml(browser).splitlines()[3:7] == [
        'profile = "typescript"',
        'target = "browser"',
        'framework = "react"',
        'bundler = "vite"',
    ]
    node = dataclasses.replace(WEB, target="node")
    assert component_toml(node).splitlines()[3:6] == [
        'profile = "typescript"',
        'target = "node"',
        'root = "web"',
    ]


def test_the_table_follows_the_last_component_and_its_subtables() -> None:
    text = policy_toml(ComponentSpec(extra=ARCHITECTURE), extra=TRAILER)
    edited, policy = add_component(text, component_toml(WEB))
    assert [component.id for component in policy.components] == ["app", "web"]
    assert policy.components[0].architecture.layers == ["api", "core"]
    head, tail = edited.split("[ci]\n")
    assert head.endswith('no_boundary_reason = "a pure library"\n\n# The CI wrapper contract.\n')
    assert head.index("[components.architecture]") < head.index('id = "web"')
    assert tail == text.split("[ci]\n")[1]


def test_a_policy_ending_with_components_gets_the_table_at_the_end() -> None:
    text = policy_toml(ComponentSpec())
    edited, policy = add_component(text, component_toml(WEB))
    assert edited == text.rstrip("\n") + "\n\n" + component_toml(WEB)
    assert policy.components[-1].id == "web"


def test_a_header_inside_a_string_falls_back_to_the_end() -> None:
    reason = 'no_boundary_reason = """\n[ci]\n"""'
    text = policy_toml(ComponentSpec()).replace(
        'no_boundary_reason = "pure computation without external boundaries"', reason
    )
    edited, policy = add_component(text, component_toml(WEB))
    assert edited == text.rstrip("\n") + "\n\n" + component_toml(WEB)
    assert policy.components[0].no_boundary_reason == "[ci]\n"


def test_an_invalid_policy_cannot_be_edited() -> None:
    with pytest.raises(EditError) as raised:
        add_component("schema_version = [\n", component_toml(WEB))
    assert [d.rule for d in raised.value.diagnostics] == ["policy.syntax"]


def test_an_invalid_component_is_reported_and_nothing_is_returned() -> None:
    bare = Draft("web", "typescript", "web", ("src",), ("tests",))
    with pytest.raises(EditError) as raised:
        add_component(policy_toml(ComponentSpec()), component_toml(bare))
    assert [d.rule for d in raised.value.diagnostics] == ["policy.invalid"]
    assert "no_boundary_reason" in str(raised.value)


def test_components_written_as_an_inline_array_cannot_take_a_table() -> None:
    base = policy_toml(ComponentSpec()).split("[[components]]")[0]
    inline = (
        'components = [{ id = "app", role = "application", profile = "python", root = ".", '
        'source = ["src"], tests = { unit = ["tests"] }, no_boundary_reason = "pure" }]\n'
    )
    text = base.replace("[repository]", inline + "\n[repository]")
    with pytest.raises(EditError) as raised:
        add_component(text, component_toml(WEB))
    assert [d.rule for d in raised.value.diagnostics] == ["policy.syntax"]
