import pytest

from quality.policy import (
    BrowserSettings,
    Bundler,
    ExecutionTarget,
    Framework,
    Profile,
    Viewport,
    parse_policy,
)
from quality.testkit.policies import ComponentSpec, policy_toml

BROWSER = 'target = "browser"\nframework = "react"\nbundler = "vite"'


def web(extra: str = BROWSER, capabilities: tuple[str, ...] = ("ui",)) -> str:
    spec = ComponentSpec(id="web", profile="typescript", capabilities=capabilities, extra=extra)
    return policy_toml(spec)


def problems(text: str) -> list[tuple[str, str]]:
    result = parse_policy(text.encode())
    assert result.policy is None
    return [(d.rule, d.message) for d in result.diagnostics]


def test_existing_components_keep_running_on_node_without_a_migration() -> None:
    policy = parse_policy(policy_toml(ComponentSpec(profile="typescript")).encode()).policy
    assert policy is not None
    (component,) = policy.components
    assert (component.target, component.framework, component.bundler) == (None, None, None)
    assert not component.in_browser
    node = parse_policy(web('target = "node"', ("library",)).encode()).policy
    assert node is not None
    assert node.components[0].target is ExecutionTarget.NODE
    assert not node.components[0].in_browser


def test_a_browser_component_declares_its_stack_and_gets_default_settings() -> None:
    policy = parse_policy(web().encode()).policy
    assert policy is not None
    (component,) = policy.components
    assert component.in_browser
    assert (component.profile, component.framework, component.bundler) == (
        Profile.TYPESCRIPT,
        Framework.REACT,
        Bundler.VITE,
    )
    assert component.browser is None
    settings = component.browser_settings
    assert settings == BrowserSettings()
    assert settings.entries == ["index.html"]
    assert settings.public_env == {}
    assert settings.viewports == {
        "narrow": Viewport(width=390, height=844),
        "wide": Viewport(width=1280, height=800),
    }
    assert settings.accessibility_tags == ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]


def test_the_browser_table_refines_the_defaults() -> None:
    table = """
[components.browser]
entries = ["index.html", "admin/index.html"]
public_env = { VITE_API_URL = "https://api.example.test", VITE_FLAG = "" }
server_modules = ["src/server/**"]
server_packages = ["pg", "@aws-sdk/client-s3"]
viewports = { phone = { width = 360, height = 740 } }
accessibility_tags = ["wcag2a", "wcag22aa", "best-practice"]
"""
    policy = parse_policy(web(BROWSER + table).encode()).policy
    assert policy is not None
    settings = policy.components[0].browser_settings
    assert settings.entries == ["index.html", "admin/index.html"]
    assert settings.public_env == {"VITE_API_URL": "https://api.example.test", "VITE_FLAG": ""}
    assert settings.server_modules == ["src/server/**"]
    assert settings.server_packages == ["pg", "@aws-sdk/client-s3"]
    assert settings.viewports == {"phone": Viewport(width=360, height=740)}
    assert settings.accessibility_tags == ["wcag2a", "wcag22aa", "best-practice"]


@pytest.mark.parametrize(
    ("profile", "extra", "capabilities", "message"),
    [
        ("python", 'target = "node"', ("library",), "'target' applies only to components of"),
        ("typescript", 'framework = "react"', ("library",), "framework: only a component with"),
        (
            "typescript",
            'target = "node"\nbundler = "vite"\n[components.browser]',
            ("library",),
            'bundler, browser: only a component with target = "browser" declares these',
        ),
        ("typescript", 'target = "browser"', ("ui",), 'declares framework = "react" and bundler'),
        ("typescript", 'target = "browser"\nframework = "react"', ("ui",), "and bundler = "),
        ("typescript", BROWSER, ("cli",), "a browser component declares the 'ui' capability"),
    ],
)
def test_an_inconsistent_target_declaration_is_invalid(
    profile: str, extra: str, capabilities: tuple[str, ...], message: str
) -> None:
    spec = ComponentSpec(profile=profile, capabilities=capabilities, extra=extra)
    ((rule, text),) = problems(policy_toml(spec))
    assert rule == "policy.invalid"
    assert text.startswith("components[0]: Value error, ")
    assert message in text


@pytest.mark.parametrize(
    ("table", "key"),
    [
        ('target = "web"', "components[0].target"),
        ('framework = "vue"', "components[0].framework"),
        ('bundler = "webpack"', "components[0].bundler"),
        ("[components.browser]\nentries = []", "components[0].browser.entries"),
        ('[components.browser]\nentries = ["main.tsx"]', "components[0].browser.entries[0]"),
        ('[components.browser]\nentries = ["../index.html"]', "components[0].browser.entries[0]"),
        ('[components.browser]\npublic_env = { API = "x" }', "components[0].browser.public_env"),
        (
            '[components.browser]\npublic_env = { VITE_lower = "x" }',
            "components[0].browser.public_env",
        ),
        (
            '[components.browser]\npublic_env = { VITE_A = "two\\nlines" }',
            "components[0].browser.public_env.VITE_A",
        ),
        ("[components.browser]\npublic_env = { VITE_A = 1 }", "components[0].browser.public_env"),
        ('[components.browser]\nserver_packages = ["Pg"]', "components[0].browser.server_pack"),
        ('[components.browser]\nserver_modules = ["/abs/**"]', "components[0].browser.server_mod"),
        ("[components.browser]\nviewports = {}", "components[0].browser.viewports"),
        (
            "[components.browser]\nviewports = { a = { width = 100, height = 800 } }",
            "components[0].browser.viewports.a.width",
        ),
        (
            "[components.browser]\nviewports = { a = { width = 800, height = 9000 } }",
            "components[0].browser.viewports.a.height",
        ),
        (
            '[components.browser]\nviewports = { a = { width = "800", height = 800 } }',
            "components[0].browser.viewports.a.width",
        ),
        ("[components.browser]\naccessibility_tags = []", "components[0].browser.accessibil"),
        (
            '[components.browser]\naccessibility_tags = ["WCAG2A"]',
            "components[0].browser.accessibility_tags[0]",
        ),
        ("[components.browser]\ncolour = 1", "components[0].browser.colour"),
    ],
)
def test_invalid_browser_values_are_reported_with_their_key(table: str, key: str) -> None:
    extra = BROWSER + "\n" + table if table.startswith("[") else table
    found = problems(web(extra))
    assert found
    assert all(rule in {"policy.invalid", "policy.unknown-key"} for rule, _ in found)
    assert any(key in message for _, message in found), found
