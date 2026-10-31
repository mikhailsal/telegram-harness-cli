"""Builders for policy documents used by tests and adapter contract tests."""

from __future__ import annotations

import json
from dataclasses import dataclass

from quality.policy import Policy, parse_policy

BASE_POLICY = """\
schema_version = 1

[repository]
mode = "{mode}"

[thresholds]
source_file_lines = {{ warning = 250, error = 400 }}
function_code_lines = {{ error = 50 }}
cyclomatic_complexity = {{ error = 10 }}
nesting_depth = {{ warning = 3, error = 4 }}
parameters = {{ warning = 5 }}
doc_page_lines = 300
doc_page_words = 2000
agents_lines = 150
agents_words = 1000
blob_bytes = 1048576

[coverage]
line = 80
branch = 70
changed_lines = 90
new_file_must_be_covered = true
"""


def _toml_list(values: tuple[str, ...]) -> str:
    return "[" + ", ".join(f'"{value}"' for value in values) + "]"


@dataclass(frozen=True, slots=True)
class ComponentSpec:
    """One `[[components]]` table; defaults describe a small Python library without boundaries."""

    id: str = "app"
    root: str = "."
    profile: str = "python"
    role: str = "application"
    source: tuple[str, ...] = ("src",)
    unit: tuple[str, ...] = ("tests",)
    integration: tuple[str, ...] = ()
    e2e: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    scenarios: str | None = None
    extra: str = ""

    def _suites(self) -> str:
        suites = [f"unit = {_toml_list(self.unit)}"]
        if self.integration:
            suites.append(f"integration = {_toml_list(self.integration)}")
        if self.e2e:
            suites.append(f"e2e = {_toml_list(self.e2e)}")
        return ", ".join(suites)

    @property
    def browser(self) -> bool:
        """Whether the component targets the browser, as `browser_spec` declares it."""
        return BROWSER_STACK in self.extra

    def toml(self) -> str:
        """Render the component as TOML."""
        lines = [
            "[[components]]",
            f'id = "{self.id}"',
            f'role = "{self.role}"',
            f'profile = "{self.profile}"',
            f'root = "{self.root}"',
            f"source = {_toml_list(self.source)}",
            f"tests = {{ {self._suites()} }}",
        ]
        if self.capabilities:
            lines.append(f"capabilities = {_toml_list(self.capabilities)}")
        else:
            lines.append('no_boundary_reason = "pure computation without external boundaries"')
        if self.scenarios is not None:
            lines.append(f'scenarios = "{self.scenarios}"')
        if self.extra:
            lines.append(self.extra.strip())
        return "\n".join(lines) + "\n"


BROWSER_STACK = 'target = "browser"\nframework = "react"\nbundler = "vite"'


def browser_spec(component: str = "web", root: str = "web", extra: str = "") -> ComponentSpec:
    """Return a React + Vite browser component (decision 0017); `extra` follows its fields."""
    return ComponentSpec(
        id=component,
        root=root,
        profile="typescript",
        capabilities=("ui",),
        extra=f"{BROWSER_STACK}\n{extra}",
    )


def policy_toml(*components: ComponentSpec, mode: str = "project", extra: str = "") -> str:
    """Render a complete policy document with the template defaults."""
    specs = components or (ComponentSpec(),)
    body = [BASE_POLICY.format(mode=mode), *(spec.toml() for spec in specs)]
    if extra:
        body.append(extra.strip() + "\n")
    return "\n".join(body)


def quarantine_toml(
    test: str,
    *scenarios: str,
    suite: str = "integration",
    expires: str = "2999-01-01",
    component: str = "app",
) -> str:
    """Render one `[[quarantine]]` table that suspends `scenarios`."""
    lines = [
        "[[quarantine]]",
        f'component = "{component}"',
        f'suite = "{suite}"',
        f"test = {json.dumps(test)}",
        'reason = "flaky in the test fixture"',
        'owner = "fixtures"',
        f"expires = {expires}",
        f"scenarios = {_toml_list(scenarios)}",
    ]
    return "\n".join(lines) + "\n"


def classification_toml(pattern: str, category: str, reason: str = "test fixture") -> str:
    """Render one `[[classification]]` table."""
    lines = [f'pattern = "{pattern}"', f'category = "{category}"', f'reason = "{reason}"']
    return "\n".join(["[[classification]]", *lines]) + "\n"


def build_policy(*components: ComponentSpec, mode: str = "project", extra: str = "") -> Policy:
    """Parse a rendered policy, failing loudly when the test document is invalid."""
    result = parse_policy(policy_toml(*components, mode=mode, extra=extra).encode())
    if result.policy is None:
        msg = f"invalid test policy: {[d.message for d in result.diagnostics]}"
        raise AssertionError(msg)
    return result.policy
