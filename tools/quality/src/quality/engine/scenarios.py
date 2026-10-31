"""Scenario manifests of a candidate: loading, static checks, stages, and reconciliation (R08).

Every active component's manifest is read from the snapshot and checked
against its policy entry, the requirement catalog, its OpenAPI document, and
the manifest of the base revision. The suites a stage needs then run, and
only their fresh results can prove a scenario (decision 0014).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from functools import cached_property

from quality.boundedio import ReportError, read_bounded
from quality.diagnostics import Diagnostic, Location
from quality.engine.scope import Scope
from quality.gitio import Repository, list_tree, read_blobs
from quality.policy import Component, Policy, Quarantine, Suite
from quality.policy.load import POLICY_FILE, parse_policy
from quality.policy.scenarios import Scenario, Stage
from quality.scenarios.manifest import MAX_MANIFEST_BYTES, parse_manifest
from quality.scenarios.openapi import load_operations
from quality.scenarios.reconcile import Ran, ScenarioSummary, reconcile
from quality.scenarios.rules import MANIFEST
from quality.scenarios.validate import (
    Subject,
    deleted_findings,
    manifest_findings,
    quarantine_findings,
)


def manifest_path(component: Component) -> str | None:
    """Return the repository path of a component's manifest, if it names one."""
    if component.scenarios is None:
        return None
    return (
        component.scenarios if component.root == "." else f"{component.root}/{component.scenarios}"
    )


def required_at(stage: Stage) -> Callable[[Scenario], bool]:
    """Return whether a scenario is mandatory at a stage; a merge needs the commit's too."""
    return lambda scenario: scenario.stage is Stage.COMMIT or stage is Stage.MERGE


def stage_suites(subject: Subject | None, stage: Stage) -> tuple[Suite, ...]:
    """Return the unit suite and the levels of the scenarios mandatory at a stage."""
    needed = required_at(stage)
    scenarios = subject.manifest.scenarios if subject is not None else []
    levels = {scenario.level for scenario in scenarios if needed(scenario)}
    return tuple(suite for suite in Suite if suite is Suite.UNIT or suite in levels)


class BaseRevision:
    """The scenario IDs of the base revision's manifests, read through Git on first use."""

    def __init__(self, repo: Repository, tree: str) -> None:
        self.repo, self.tree = repo, tree

    @cached_property
    def _entries(self) -> dict[str, tuple[str, int]]:
        listed = list_tree(self.repo, self.tree)
        return {entry.path: (entry.oid, entry.size) for entry in listed if entry.is_file}

    def _read(self, path: str) -> bytes | None:
        entry = self._entries.get(path)
        if entry is None or entry[1] > MAX_MANIFEST_BYTES:
            return None
        return b"".join(data for _, data in read_blobs(self.repo, [entry]))

    @cached_property
    def _policy(self) -> Policy | None:
        data = self._read(POLICY_FILE)
        return parse_policy(data).policy if data is not None else None

    def scenario_ids(self, component: str) -> frozenset[str]:
        """Return the scenario IDs the component's base manifest declares; none if unreadable."""
        policy = self._policy
        found = [c for c in policy.components if c.id == component] if policy else []
        path = manifest_path(found[0]) if found else None
        data = self._read(path) if path is not None else None
        loaded = parse_manifest(data, path, component).manifest if data and path else None
        return frozenset(s.id for s in loaded.scenarios) if loaded is not None else frozenset()


@dataclass(frozen=True, slots=True)
class Manifests:
    """The parsed manifests of the active components, by component ID, and their findings."""

    subjects: Mapping[str, Subject]
    diagnostics: tuple[Diagnostic, ...]


def _missing(component: Component, quarantine: Iterable[Quarantine]) -> list[Diagnostic]:
    found = quarantine_findings(component, {}, list(quarantine))
    if component.capabilities:
        declared = ", ".join(component.capabilities)
        message = f"'{component.id}' declares {declared} but names no scenario manifest"
        location = Location(POLICY_FILE)
        found.append(MANIFEST.diagnostic(message, location=location, component=component.id))
    return found


def _subject(component: Component, path: str, scope: Scope) -> Subject | list[Diagnostic]:
    sizes = {entry.path: entry.size for entry in scope.snapshot.entries if entry.is_file}
    location = Location(path)

    def invalid(message: str) -> list[Diagnostic]:
        return [MANIFEST.diagnostic(message, location=location, component=component.id)]

    if sizes.get(path, MAX_MANIFEST_BYTES + 1) > MAX_MANIFEST_BYTES:
        return invalid(f"the scenario manifest '{path}' is not a file of at most 1 MiB")
    data = read_bounded(scope.snapshot.root / path, MAX_MANIFEST_BYTES)
    loaded = parse_manifest(data, path, component.id)
    if loaded.manifest is None:
        return list(loaded.diagnostics)
    openapi = loaded.manifest.openapi
    if openapi is None:
        return Subject(component, loaded.manifest, path)
    document = openapi if component.root == "." else f"{component.root}/{openapi}"
    if document not in sizes:
        return invalid(f"the OpenAPI document '{document}' is not a file of the candidate")
    try:
        operations = load_operations(scope.snapshot.root / document)
    except ReportError as error:
        return invalid(f"the OpenAPI document '{document}' is unusable: {error}")
    return Subject(component, loaded.manifest, path, operations)


def load_manifests(repo: Repository, base_tree: str, scope: Scope) -> Manifests:
    """Read and statically check the manifest of every active component."""
    base = BaseRevision(repo, base_tree)
    quarantine = scope.policy.quarantine
    subjects: dict[str, Subject] = {}
    found: list[Diagnostic] = []
    for component in scope.active:
        path = manifest_path(component)
        if path is None:
            found.extend(_missing(component, quarantine))
            continue
        subject = _subject(component, path, scope)
        if isinstance(subject, list):
            found.extend(subject)
            continue
        subjects[component.id] = subject
        found.extend(manifest_findings(subject, quarantine))
        found.extend(deleted_findings(subject, base.scenario_ids(component.id)))
    return Manifests(subjects, tuple(found))


def _ran_levels(results: Ran) -> Callable[[Scenario], bool]:
    return lambda scenario: scenario.level in results


def reconciled(
    manifests: Manifests,
    ran: Mapping[str, Ran],
    required: Callable[[Scenario], bool] | None,
    quarantine: list[Quarantine],
) -> tuple[list[Diagnostic], list[ScenarioSummary]]:
    """Prove the scenarios of the components that ran; None requires the levels that ran."""
    found: list[Diagnostic] = []
    summaries: list[ScenarioSummary] = []
    for component, results in ran.items():
        subject = manifests.subjects.get(component)
        if subject is None:
            continue
        needed = required or _ran_levels(results)
        diagnostics, summary = reconcile(
            subject.manifest, subject.path, needed, results, quarantine
        )
        found.extend(diagnostics)
        summaries.append(summary)
    return found, summaries
