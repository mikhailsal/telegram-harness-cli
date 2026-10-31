"""Builders for scenario manifests used by tests."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass

from quality.policy import Component
from quality.policy.scenarios import ScenarioManifest
from quality.scenarios.manifest import parse_manifest
from quality.scenarios.validate import Subject
from quality.testkit.policies import BROWSER_STACK, ComponentSpec, build_policy

MANIFEST_PATH = "scenarios.toml"


def _strings(values: tuple[str, ...]) -> str:
    return "[" + ", ".join(json.dumps(value) for value in values) + "]"


@dataclass(frozen=True, slots=True)
class ScenarioSpec:
    """One `[[scenarios]]` table; the defaults describe a unit library scenario."""

    id: str = "public-api"
    tests: tuple[str, ...] = ("tests.test_core::test_add",)
    capability: str = "library"
    level: str = "unit"
    stage: str = "commit"
    requirements: tuple[str, ...] = ()
    operations: tuple[str, ...] = ()
    enhanced: str | None = None
    boundary: str | None = None

    def toml(self) -> str:
        """Render the scenario as TOML."""
        lines = [
            "[[scenarios]]",
            f'id = "{self.id}"',
            'summary = "a behavior of the fixture"',
            f'capability = "{self.capability}"',
            f'level = "{self.level}"',
            f'stage = "{self.stage}"',
            f"tests = {_strings(self.tests)}",
            f"requirements = {_strings(self.requirements)}",
            f"operations = {_strings(self.operations)}",
        ]
        if self.enhanced is not None:
            lines.append(f'enhanced = "{self.enhanced}"')
        if self.boundary is not None:
            lines.append(f"boundary = {json.dumps(self.boundary)}")
        return "\n".join(lines) + "\n"


def manifest_toml(
    *scenarios: ScenarioSpec,
    component: str = "app",
    openapi: str | None = None,
    not_applicable: Mapping[str, str] | None = None,
    retired: Mapping[str, str] | None = None,
) -> str:
    """Render a complete manifest of `component`."""
    body = [f'schema_version = 1\ncomponent = "{component}"\n']
    if openapi is not None:
        body[0] += f'openapi = "{openapi}"\n'
    body.extend(scenario.toml() for scenario in scenarios)
    for requirement, reason in (not_applicable or {}).items():
        body.append(
            f'[[not_applicable]]\nrequirement = "{requirement}"\nreason = {json.dumps(reason)}\n'
        )
    for identifier, reason in (retired or {}).items():
        body.append(f'[[retired]]\nid = "{identifier}"\nreason = {json.dumps(reason)}\n')
    return "\n".join(body)


def _parsed(text: str, component: str) -> ScenarioManifest:
    loaded = parse_manifest(text.encode(), MANIFEST_PATH, component)
    if loaded.manifest is None:
        msg = f"invalid test manifest: {[d.message for d in loaded.diagnostics]}"
        raise AssertionError(msg)
    return loaded.manifest


def build_manifest(
    *scenarios: ScenarioSpec, component: str = "app", openapi: str | None = None
) -> ScenarioManifest:
    """Parse a rendered manifest, failing loudly when the test document is invalid."""
    return _parsed(manifest_toml(*scenarios, component=component, openapi=openapi), component)


def scenario_component(*capabilities: str, extra: str = "", browser: bool = False) -> Component:
    """Return component `app` with integration and e2e suites and a manifest.

    It declares `capabilities`, or cli when none are given; a `browser`
    component is a React + Vite one (decision 0017).
    """
    spec = ComponentSpec(
        profile="typescript" if browser else "python",
        capabilities=capabilities or ("cli",),
        integration=("integration",),
        e2e=("e2e",),
        scenarios=MANIFEST_PATH,
        extra=f"{BROWSER_STACK}\n{extra}" if browser else extra,
    )
    return build_policy(spec).components[0]


def manifest_subject(
    owner: Component,
    *scenarios: ScenarioSpec,
    operations: tuple[str, ...] | None = None,
    not_applicable: Mapping[str, str] | None = None,
    retired: Mapping[str, str] | None = None,
) -> Subject:
    """Return what the validator checks: `owner` with a manifest of `scenarios`.

    With `operations`, the manifest names the OpenAPI document `openapi.json`,
    which publishes those operations.
    """
    openapi = None if operations is None else "openapi.json"
    text = manifest_toml(
        *scenarios, openapi=openapi, not_applicable=not_applicable, retired=retired
    )
    return Subject(owner, _parsed(text, owner.id), MANIFEST_PATH, operations)
