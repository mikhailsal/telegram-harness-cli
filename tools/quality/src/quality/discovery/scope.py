"""Which components a change requires to be tested again (R06)."""

from __future__ import annotations

from collections.abc import Iterable

from quality.discovery.classify import owner, under
from quality.discovery.filetypes import FileKind, detect
from quality.policy import Component, Policy

CORE_ROOT = "tools/quality"
POLICY_FILE = "quality.toml"


def _with_dependents(policy: Policy, direct: set[str]) -> set[str]:
    affected = set(direct)
    grew = True
    while grew:
        grew = False
        for component in policy.components:
            if component.id not in affected and affected.intersection(component.depends_on):
                affected.add(component.id)
                grew = True
    return affected


def affected_components(policy: Policy, changed: Iterable[str]) -> tuple[Component, ...]:
    """Return the components whose complete unit suites these changed paths require.

    A path inside a component affects that component and, transitively, every
    component that depends on it. The policy, the quality core, and every other path
    outside all components except documentation affect every component, because
    their effect on tests cannot be bounded.
    """
    direct: set[str] = set()
    for path in changed:
        if path == POLICY_FILE or under(path, CORE_ROOT):
            return tuple(policy.components)
        component = owner(policy, path)
        if component is not None:
            direct.add(component.id)
        elif detect(path).kind is not FileKind.DOC:
            return tuple(policy.components)
    affected = _with_dependents(policy, direct)
    return tuple(component for component in policy.components if component.id in affected)
