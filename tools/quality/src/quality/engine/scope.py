"""What a candidate contains and changes, and which components its gates cover."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from quality.adapters.native import ToolFailedError
from quality.diagnostics import Diagnostic, Kind, Rule, Severity
from quality.discovery import (
    Inventory,
    Region,
    active_components,
    affected_components,
    build_inventory,
)
from quality.engine.profiles import ProfileTests
from quality.gitio import Candidate, FileChange, Repository, Snapshot, changed_lines, tree_changes
from quality.policy import Category, Component, Policy, Profile, cross_validate, load_policy
from quality.testing import StaticRequest

UNAVAILABLE = Rule(
    id="profiles.unavailable",
    title="Profile gates are not available in this build",
    requirement="R01",
    explanation=(
        "An active component uses a profile whose adapter is not implemented yet, so its "
        "formatting, lint, type, structure, and test gates cannot run. Nothing passes silently."
    ),
    remedy="Use a supported profile or update the boilerplate to a version that provides it.",
    kind=Kind.ENVIRONMENT,
)
RULES = (UNAVAILABLE,)

CHECKED = frozenset({Category.SOURCE, Category.TESTS})


def unavailable(component: Component, what: str = "adapter for it") -> Diagnostic:
    """Report that a component's profile has no adapter in this build."""
    message = f"'{component.id}' uses the {component.profile} profile; this build has no {what}"
    return UNAVAILABLE.diagnostic(message, component=component.id)


def checked_policy(root: Path, rules: Iterable[str]) -> Policy | tuple[Diagnostic, ...]:
    """Load and cross-validate the snapshot's policy, or return why it is unusable."""
    loaded = load_policy(root)
    if loaded.policy is None:
        return tuple(loaded.diagnostics)
    problems = cross_validate(loaded.policy, frozenset(rules))
    return tuple(problems) if problems else loaded.policy


@dataclass(frozen=True, slots=True)
class Changes:
    """What the candidate changes: lines per path, the files it adds, and every touched path."""

    lines: Mapping[str, frozenset[int]]
    added: frozenset[str]
    paths: frozenset[str] = frozenset()


def changed_paths(changes: Iterable[FileChange]) -> list[str]:
    """Every path a change touches, including the old side of a rename."""
    paths: list[str] = []
    for change in changes:
        paths.append(change.path)
        if change.old_path is not None:
            paths.append(change.old_path)
    return paths


def added_files(changes: Iterable[FileChange]) -> frozenset[str]:
    """Files whose every line is new: additions and replacements of non-files."""
    return frozenset(
        change.path
        for change in changes
        if change.status != "D"
        and change.new_is_file
        and (change.status == "A" or not change.old_is_file)
    )


def selected_components(
    policy: Policy, paths: list[str], *, everything: bool
) -> tuple[Component, ...]:
    """Active components the change affects, or every active component."""
    active = active_components(policy, paths)
    if everything:
        return active
    affected = {component.id for component in affected_components(policy, paths)}
    return tuple(component for component in active if component.id in affected)


@dataclass(frozen=True, slots=True)
class Scope:
    """A materialized candidate with its inventory, changes, and covered components."""

    policy: Policy
    snapshot: Snapshot
    inventory: Inventory
    changes: Changes
    active: tuple[Component, ...]
    selected: tuple[Component, ...]

    def files(self, component: Component) -> tuple[str, ...]:
        """Return the component's source and test files in inventory order."""
        return tuple(
            record.path
            for record in self.inventory.files
            if record.component == component.id and record.category in CHECKED
        )

    def sources(self, component: Component) -> frozenset[str]:
        """Return the component's checked files inside its source roots."""
        return frozenset(
            record.path
            for record in self.inventory.files
            if record.component == component.id
            and record.category in CHECKED
            and record.region is Region.SOURCE
        )

    def static_request(self, component: Component) -> StaticRequest:
        """Name the files a component's static gates check: its code, config scripts, and rest."""
        owned = [record for record in self.inventory.files if record.component == component.id]
        configs = tuple(
            record.path
            for record in owned
            if record.category is Category.CONFIG and record.language is not None
        )
        resources = tuple(
            record.path
            for record in owned
            if record.category not in CHECKED | {Category.GENERATED} and record.path not in configs
        )
        snapshot = self.snapshot
        return StaticRequest(
            component, snapshot.root, snapshot.work, self.files(component), configs, resources
        )


def candidate_scope(
    repo: Repository, candidate: Candidate, snapshot: Snapshot, policy: Policy, *, everything: bool
) -> Scope:
    """Inventory a materialized candidate and select the components its change affects."""
    inventory = build_inventory(policy, snapshot.root, snapshot.entries)
    diff = tree_changes(repo, candidate.base_tree, candidate.tree)
    paths = changed_paths(diff)
    changes = Changes(changed_lines(repo, candidate), added_files(diff), frozenset(paths))
    active = active_components(policy, paths)
    selected = selected_components(policy, paths, everything=everything)
    return Scope(policy, snapshot, inventory, changes, active, selected)


def prepare_components[A: ProfileTests](
    adapters: Mapping[Profile, A], components: Iterable[Component], snapshot: Snapshot
) -> tuple[list[tuple[Component, A]], list[Diagnostic]]:
    """Prepare each component once; only components without errors are ready."""
    ready: list[tuple[Component, A]] = []
    found: list[Diagnostic] = []
    for component in components:
        adapter = adapters.get(component.profile)
        if adapter is None:
            found.append(unavailable(component))
            continue
        try:
            problems = adapter.prepare(component, snapshot.root, snapshot.work)
        except ToolFailedError as error:
            found.append(error.diagnostic)
            continue
        found.extend(problems)
        if not any(problem.severity is Severity.ERROR for problem in problems):
            ready.append((component, adapter))
    return ready, found
