"""Static checks of a scenario manifest against its component and the catalog (R08).

These checks need no test run: what each scenario refers to, whether every
obligation of the declared capabilities, OpenAPI operations, and enhanced
profiles has a scenario, and whether a scenario of the base revision vanished.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass

from quality.diagnostics import Diagnostic, Location, Rule
from quality.policy import Capability, Component, Enhancement, Quarantine, Suite
from quality.policy.load import POLICY_FILE
from quality.policy.scenarios import Scenario, ScenarioManifest
from quality.scenarios.catalog import Requirement, requirement, requirements
from quality.scenarios.rules import AMBIGUOUS, DELETED, ENHANCED, LEVEL, REFERENCE, UNCOVERED
from quality.testing import suite_roots


@dataclass(frozen=True, slots=True)
class Subject:
    """A component's manifest at a repository path, and its OpenAPI operations if it has any."""

    component: Component
    manifest: ScenarioManifest
    path: str
    operations: tuple[str, ...] | None = None

    def report(self, rule: Rule, message: str) -> Diagnostic:
        """Create a finding located at the manifest."""
        location = Location(self.path)
        return rule.diagnostic(message, location=location, component=self.component.id)


def _duplicates(values: Iterable[str]) -> list[str]:
    return sorted(value for value, count in Counter(values).items() if count > 1)


def _levels(levels: Iterable[Suite]) -> str:
    return ", ".join(suite.value for suite in Suite if suite in frozenset(levels))


def _identities(subject: Subject) -> Iterator[Diagnostic]:
    active = [scenario.id for scenario in subject.manifest.scenarios]
    retired = [entry.id for entry in subject.manifest.retired]
    for duplicate in _duplicates(active):
        yield subject.report(AMBIGUOUS, f"scenario '{duplicate}' is declared more than once")
    for duplicate in _duplicates(retired):
        yield subject.report(AMBIGUOUS, f"scenario '{duplicate}' is retired more than once")
    for both in sorted(set(active) & set(retired)):
        yield subject.report(AMBIGUOUS, f"scenario '{both}' is both active and retired")


def _requirements(subject: Subject, scenario: Scenario) -> Iterator[Diagnostic]:
    name = f"scenario '{scenario.id}'"
    for identifier in scenario.requirements:
        found = requirement(identifier)
        if found is None:
            yield subject.report(REFERENCE, f"{name} names the unknown requirement '{identifier}'")
            continue
        capability, needed = found
        if needed.browser and not subject.component.in_browser:
            message = f"{name} proves '{identifier}', which only a browser component proves"
            yield subject.report(REFERENCE, message)
        elif capability is not scenario.capability:
            message = (
                f"{name} proves '{scenario.capability}', but '{identifier}' is a "
                f"requirement of '{capability}'"
            )
            yield subject.report(REFERENCE, message)
        elif scenario.level not in needed.levels:
            message = (
                f"{name} proves '{identifier}' with the {scenario.level} suite; "
                f"it accepts {_levels(needed.levels)}"
            )
            yield subject.report(LEVEL, message)


def _operations(subject: Subject, scenario: Scenario) -> Iterator[Diagnostic]:
    name = f"scenario '{scenario.id}'"
    if scenario.operations and subject.operations is None:
        message = f"{name} names operations, but the manifest names no OpenAPI document"
        yield subject.report(REFERENCE, message)
        return
    for operation in scenario.operations:
        if operation not in (subject.operations or ()):
            message = (
                f"{name} names '{operation}', which is not an operation of the OpenAPI document"
            )
            yield subject.report(REFERENCE, message)


def _scenario(subject: Subject, scenario: Scenario) -> Iterator[Diagnostic]:
    component, name = subject.component, f"scenario '{scenario.id}'"
    if scenario.capability not in component.capabilities:
        message = f"{name} proves '{scenario.capability}', which '{component.id}' does not declare"
        yield subject.report(REFERENCE, message)
    if not suite_roots(component, scenario.level):
        message = f"{name} is proved by the {scenario.level} suite; '{component.id}' has none"
        yield subject.report(REFERENCE, message)
    if scenario.enhanced is not None and scenario.enhanced not in component.enhanced:
        message = f"{name} is tagged '{scenario.enhanced}', which '{component.id}' does not enable"
        yield subject.report(REFERENCE, message)
    if component.in_browser and scenario.boundary is None:
        message = f"{name} names no tested boundary; every scenario of a browser component does"
        yield subject.report(REFERENCE, message)
    if scenario.enhanced is Enhancement.PERFORMANCE and scenario.level is Suite.UNIT:
        yield subject.report(LEVEL, f"{name} measures performance in the unit suite")
    for duplicate in _duplicates(scenario.tests):
        yield subject.report(AMBIGUOUS, f"{name} names the test '{duplicate}' more than once")
    yield from _requirements(subject, scenario)
    yield from _operations(subject, scenario)


def _obligation(
    subject: Subject, needed: Requirement, covered: set[str], excused: set[str]
) -> Iterator[Diagnostic]:
    if needed.id in covered and needed.id in excused:
        message = f"'{needed.id}' is both proved by a scenario and declared not applicable"
        yield subject.report(AMBIGUOUS, message)
    elif needed.id in excused and not needed.optional:
        message = f"'{needed.id}' ({needed.summary}) always applies; it cannot be not applicable"
        yield subject.report(UNCOVERED, message)
    elif needed.id not in covered | excused:
        alternative = ", or declare it not applicable with a reason" if needed.optional else ""
        message = f"no scenario proves '{needed.id}': {needed.summary}{alternative}"
        yield subject.report(UNCOVERED, message)


def _obligations(subject: Subject) -> Iterator[Diagnostic]:
    component, manifest = subject.component, subject.manifest
    covered = {
        identifier for scenario in manifest.scenarios for identifier in scenario.requirements
    }
    excused = [entry.requirement for entry in manifest.not_applicable]
    for duplicate in _duplicates(excused):
        yield subject.report(AMBIGUOUS, f"'{duplicate}' is declared not applicable more than once")
    for capability in dict.fromkeys(component.capabilities):
        if not any(scenario.capability is capability for scenario in manifest.scenarios):
            message = f"'{component.id}' declares '{capability}', but no scenario proves it"
            yield subject.report(UNCOVERED, message)
        for needed in requirements(capability, browser=component.in_browser):
            yield from _obligation(subject, needed, covered, set(excused))
    yield from _foreign_excuses(subject, excused)


def _foreign_excuses(subject: Subject, excused: list[str]) -> Iterator[Diagnostic]:
    component = subject.component
    for identifier in excused:
        found = requirement(identifier)
        if found is None or found[0] not in component.capabilities:
            message = (
                f"'{identifier}' is not a requirement of a capability '{component.id}' declares"
            )
            yield subject.report(REFERENCE, message)


def _inventory(subject: Subject) -> Iterator[Diagnostic]:
    manifest = subject.manifest
    if manifest.openapi is not None and Capability.HTTP_API not in subject.component.capabilities:
        message = f"'{subject.component.id}' has an OpenAPI document but not 'http-api'"
        yield subject.report(REFERENCE, message)
    named = {operation for scenario in manifest.scenarios for operation in scenario.operations}
    for operation in subject.operations or ():
        if operation not in named:
            yield subject.report(
                UNCOVERED, f"no scenario names the OpenAPI operation '{operation}'"
            )


def _enhanced(subject: Subject) -> Iterator[Diagnostic]:
    component = subject.component
    for enhancement in dict.fromkeys(component.enhanced):
        if enhancement is Enhancement.MUTATION:
            message = f"'{component.id}' enables mutation testing, which has no tested profile"
            yield subject.report(ENHANCED, message)
        elif not any(scenario.enhanced is enhancement for scenario in subject.manifest.scenarios):
            message = f"'{component.id}' enables '{enhancement}', but no scenario is tagged with it"
            yield subject.report(UNCOVERED, message)


def quarantine_findings(
    component: Component, levels: dict[str, Suite], quarantine: Sequence[Quarantine]
) -> list[Diagnostic]:
    """Check that quarantine entries suspend only scenarios of their own suite."""
    location = Location(POLICY_FILE)
    return [
        REFERENCE.diagnostic(
            f"the quarantine of '{entry.test}' names '{name}', which is not a scenario "
            f"of the {entry.suite} suite of '{component.id}'",
            location=location,
            component=component.id,
        )
        for entry in quarantine
        if entry.component == component.id
        for name in entry.scenarios
        if levels.get(name) is not entry.suite
    ]


def manifest_findings(subject: Subject, quarantine: Sequence[Quarantine]) -> list[Diagnostic]:
    """Return every static problem of a parsed manifest."""
    found = list(_identities(subject))
    for scenario in subject.manifest.scenarios:
        found.extend(_scenario(subject, scenario))
    found.extend(_obligations(subject))
    found.extend(_inventory(subject))
    found.extend(_enhanced(subject))
    levels = {scenario.id: scenario.level for scenario in subject.manifest.scenarios}
    found.extend(quarantine_findings(subject.component, levels, quarantine))
    return found


def deleted_findings(subject: Subject, previous: Iterable[str]) -> list[Diagnostic]:
    """Report scenarios of the base revision that are neither kept nor retired."""
    manifest = subject.manifest
    kept = {scenario.id for scenario in manifest.scenarios} | {
        entry.id for entry in manifest.retired
    }
    return [
        subject.report(
            DELETED,
            f"scenario '{name}' of the base revision is neither in the manifest nor retired",
        )
        for name in sorted(set(previous) - kept)
    ]
