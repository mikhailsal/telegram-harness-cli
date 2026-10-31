"""Cross-field policy validation that a schema alone cannot express."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Iterator
from itertools import combinations, product

from quality.diagnostics import Diagnostic, Kind, Location, Rule
from quality.policy.load import POLICY_FILE
from quality.policy.models import Category, Component, Policy, Suite

POLICY_CONFLICT = Rule(
    id="policy.conflict",
    title="Policy declarations conflict",
    requirement="R02",
    explanation=(
        "Component IDs must be unique, component roots must differ, and no path may be "
        "owned by two components or be both a source and a test root. A test is "
        "quarantined at most once, and never in a unit suite."
    ),
    remedy="Give each component a distinct root and disjoint source and test paths.",
    kind=Kind.USAGE,
)
POLICY_REFERENCE = Rule(
    id="policy.reference",
    title="Policy refers to something that does not exist",
    requirement="R02",
    explanation=(
        "Dependencies, architecture edges, exceptions, and quarantine entries must refer "
        "to declared items."
    ),
    remedy="Declare the referenced item or correct the reference.",
    kind=Kind.USAGE,
)
RULES = (POLICY_CONFLICT, POLICY_REFERENCE)


def _at() -> Location:
    return Location(POLICY_FILE)


def owned_paths(component: Component) -> list[str]:
    """Repository-relative source and test roots of a component."""
    prefix = "" if component.root == "." else component.root + "/"
    return [prefix + path for path in [*component.source, *component.tests.all_paths()]]


def _overlaps(left: str, right: str) -> bool:
    return left == right or left.startswith(right + "/") or right.startswith(left + "/")


def _duplicates(values: Iterable[str]) -> list[str]:
    return sorted(value for value, count in Counter(values).items() if count > 1)


def _component_conflicts(policy: Policy) -> Iterator[Diagnostic]:
    for dup in _duplicates(c.id for c in policy.components):
        yield POLICY_CONFLICT.diagnostic(f"duplicate component id '{dup}'", location=_at())
    for dup in _duplicates(c.root for c in policy.components):
        yield POLICY_CONFLICT.diagnostic(
            f"several components share the root '{dup}'", location=_at()
        )
    for left, right in combinations(policy.components, 2):
        for a, b in product(owned_paths(left), owned_paths(right)):
            if _overlaps(a, b):
                message = f"'{a}' of '{left.id}' overlaps '{b}' of '{right.id}'"
                yield POLICY_CONFLICT.diagnostic(message, location=_at(), component=left.id)


def _internal_conflicts(component: Component) -> Iterator[Diagnostic]:
    tests = component.tests.all_paths()
    for dup in _duplicates([*component.source, *tests]):
        message = f"'{dup}' is declared more than once"
        yield POLICY_CONFLICT.diagnostic(message, location=_at(), component=component.id)
    for source in component.source:
        for test in tests:
            if source != test and _overlaps(source, test):
                message = f"source root '{source}' overlaps test root '{test}'"
                yield POLICY_CONFLICT.diagnostic(message, location=_at(), component=component.id)


def _architecture(component: Component) -> Iterator[Diagnostic]:
    names = [module.name for module in component.architecture.modules]
    for dup in _duplicates(names):
        message = f"module '{dup}' is declared more than once"
        yield POLICY_CONFLICT.diagnostic(message, location=_at(), component=component.id)
    for dup in _duplicates(component.architecture.layers):
        message = f"layer '{dup}' is listed more than once"
        yield POLICY_CONFLICT.diagnostic(message, location=_at(), component=component.id)
    for name in component.architecture.layers:
        if name not in names:
            message = f"layers refer to undeclared module '{name}'"
            yield POLICY_REFERENCE.diagnostic(message, location=_at(), component=component.id)
    for edge in component.architecture.forbidden:
        for name in (edge.source, edge.target):
            if name not in names:
                message = f"forbidden edge refers to undeclared module '{name}'"
                yield POLICY_REFERENCE.diagnostic(message, location=_at(), component=component.id)
        if edge.source == edge.target:
            message = f"forbidden edge from '{edge.source}' to itself"
            yield POLICY_CONFLICT.diagnostic(message, location=_at(), component=component.id)


def _dependencies(policy: Policy) -> Iterator[Diagnostic]:
    ids = {c.id for c in policy.components}
    for component in policy.components:
        for dep in component.depends_on:
            if dep not in ids:
                message = f"depends on undeclared component '{dep}'"
                yield POLICY_REFERENCE.diagnostic(message, location=_at(), component=component.id)
            elif dep == component.id:
                yield POLICY_CONFLICT.diagnostic(
                    "component depends on itself", location=_at(), component=component.id
                )


def _exceptions(policy: Policy, known_rules: frozenset[str]) -> Iterator[Diagnostic]:
    keys = [(e.rule, e.path, e.symbol or "") for e in policy.exceptions]
    for rule, path, symbol in sorted({k for k in keys if keys.count(k) > 1}):
        message = f"duplicate exception for '{rule}' at '{path}' {symbol}".rstrip()
        yield POLICY_CONFLICT.diagnostic(message, location=_at())
    for exception in policy.exceptions:
        if exception.rule not in known_rules:
            message = f"exception refers to unknown rule '{exception.rule}'"
            yield POLICY_REFERENCE.diagnostic(message, location=_at())


def _classification(policy: Policy) -> Iterator[Diagnostic]:
    for dup in _duplicates(c.pattern for c in policy.classification):
        yield POLICY_CONFLICT.diagnostic(
            f"classification pattern '{dup}' is declared twice", location=_at()
        )
    for entry in policy.classification:
        if entry.category in {Category.SOURCE, Category.TESTS}:
            message = (
                f"classification pattern '{entry.pattern}' assigns '{entry.category}'; "
                "source and test files are declared by component roots"
            )
            yield POLICY_CONFLICT.diagnostic(message, location=_at())


def _quarantine(policy: Policy) -> Iterator[Diagnostic]:
    components = {component.id: component for component in policy.components}
    keys = [(entry.component, entry.suite.value, entry.test) for entry in policy.quarantine]
    for component, suite, test in sorted({key for key in keys if keys.count(key) > 1}):
        message = f"'{test}' of the {suite} suite is quarantined more than once"
        yield POLICY_CONFLICT.diagnostic(message, location=_at(), component=component)
    for entry in policy.quarantine:
        component = components.get(entry.component)
        if component is None:
            message = f"quarantine refers to undeclared component '{entry.component}'"
            yield POLICY_REFERENCE.diagnostic(message, location=_at())
        elif entry.suite is Suite.UNIT:
            message = f"'{entry.test}' is a unit test; unit tests cannot be quarantined"
            yield POLICY_CONFLICT.diagnostic(message, location=_at(), component=component.id)
        elif not component.tests.of(entry.suite):
            message = f"quarantine refers to the {entry.suite} suite, which is not declared"
            yield POLICY_REFERENCE.diagnostic(message, location=_at(), component=component.id)


def cross_validate(policy: Policy, known_rules: frozenset[str]) -> tuple[Diagnostic, ...]:
    """Return every cross-field problem of a schema-valid policy."""
    found: list[Diagnostic] = [*_component_conflicts(policy), *_dependencies(policy)]
    for component in policy.components:
        found.extend(_internal_conflicts(component))
        found.extend(_architecture(component))
    found.extend(_exceptions(policy, known_rules))
    found.extend(_classification(policy))
    found.extend(_quarantine(policy))
    return tuple(found)
