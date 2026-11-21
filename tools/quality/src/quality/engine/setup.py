"""`quality bootstrap` and `quality doctor` over the working tree (decision 0010).

Bootstrap downloads what offline checks need and installs the managed hooks;
doctor verifies the same without downloading or changing anything. Both read the
components from the staged candidate in an isolated snapshot, exactly as the
pre-commit check will, so untracked files cannot hide or cause a problem. Only
the components that check would activate are set up: a dormant helper bundle
needs no toolchain until the candidate changes it or an application uses its
profile (decision 0003).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from quality.ci import Lane, in_lane
from quality.diagnostics import Diagnostic, Severity
from quality.discovery import active_components
from quality.engine.common import tool_findings
from quality.engine.profiles import ProfileSetup
from quality.engine.scope import changed_paths, checked_policy, unavailable
from quality.gitio import Candidate, GitioError, Repository, materialize, staged, tree_changes
from quality.hooks import HookConflictError, HookName, inspect, install, status_diagnostics
from quality.policy import Component, Policy, Profile

type Step = Callable[[ProfileSetup, Component, Path], list[Diagnostic]]


@dataclass(slots=True)
class SetupReport:
    """Findings, and one line per thing that was done or verified."""

    diagnostics: list[Diagnostic] = field(default_factory=list[Diagnostic])
    lines: list[str] = field(default_factory=list[str])

    def merge(self, other: SetupReport) -> None:
        """Append another report's findings and lines."""
        self.diagnostics.extend(other.diagnostics)
        self.lines.extend(other.lines)


def _provision(setup: ProfileSetup, component: Component, root: Path) -> list[Diagnostic]:
    return setup.provision(component, root)


def _probe(setup: ProfileSetup, component: Component, root: Path) -> list[Diagnostic]:
    return setup.probe(component, root)


@dataclass(frozen=True, slots=True)
class SetupTarget:
    """An exact candidate and the optional gate lane to provision."""

    repo: Repository
    candidate: Candidate
    lane: Lane | None = None

    def components(self, policy: Policy) -> tuple[Component, ...]:
        """Select active components of the requested lane from the candidate's whole diff."""
        changes = tree_changes(self.repo, self.candidate.base_tree, self.candidate.tree)
        active = active_components(policy, changed_paths(changes))
        return tuple(component for component in active if in_lane(component, self.lane))

    def profiles(self, rules: Iterable[str]) -> SetupReport:
        """List the profile names before any profile tools need to be present."""
        try:
            with materialize(self.repo, self.candidate.tree) as snapshot:
                policy = checked_policy(snapshot.root, rules)
                if isinstance(policy, tuple):
                    return SetupReport(list(policy))
                lines = sorted({str(component.profile) for component in self.components(policy)})
            self.candidate.verify_unchanged()
        except GitioError as error:
            return SetupReport([error.diagnostic])
        return SetupReport(lines=lines)


def _components(
    root: Path,
    target: SetupTarget,
    setups: Mapping[Profile, ProfileSetup],
    rules: Iterable[str],
    step: Step,
) -> SetupReport:
    report = SetupReport()
    policy = checked_policy(root, rules)
    if isinstance(policy, tuple):
        report.diagnostics.extend(policy)
        return report
    active = {component.id for component in target.components(policy)}
    for component in policy.components:
        if not in_lane(component, target.lane):
            continue
        if component.id not in active:
            report.lines.append(f"{component.id}: dormant, so its {component.profile} tools wait")
            continue
        setup = setups.get(component.profile)
        if setup is None:
            report.diagnostics.append(unavailable(component, "environment setup for it"))
            continue
        problems = tool_findings(
            lambda setup=setup, component=component: step(setup, component, root)
        )
        report.diagnostics.extend(problems)
        if all(problem.severity is not Severity.ERROR for problem in problems):
            report.lines.append(f"{component.id}: the {component.profile} environment is ready")
    return report


@dataclass(frozen=True, slots=True)
class Staged:
    """The repository and the index whose candidate the hook will check."""

    repo: Repository
    index: Path | None = None


def each_component(
    target: Staged, setups: Mapping[Profile, ProfileSetup], rules: Iterable[str], step: Step
) -> SetupReport:
    """Run one step for every active component of the staged candidate, as checks see it."""
    repo = target.repo
    try:
        candidate = staged(repo, target.index)
        return _setup_candidate(SetupTarget(repo, candidate), setups, rules, step)
    except GitioError as error:
        return SetupReport([error.diagnostic])


def provision_components(
    target: Staged, setups: Mapping[Profile, ProfileSetup], rules: Iterable[str]
) -> SetupReport:
    """Download the interpreters and locked packages every component needs."""
    return each_component(target, setups, rules, _provision)


def _setup_candidate(
    target: SetupTarget,
    setups: Mapping[Profile, ProfileSetup],
    rules: Iterable[str],
    step: Step,
) -> SetupReport:
    with materialize(target.repo, target.candidate.tree) as snapshot:
        return _components(snapshot.root, target, setups, rules, step)


def provision_candidate(
    target: SetupTarget,
    setups: Mapping[Profile, ProfileSetup],
    rules: Iterable[str],
) -> SetupReport:
    """Provision a gate candidate from its actual tree and base."""
    try:
        report = _setup_candidate(target, setups, rules, _provision)
        target.candidate.verify_unchanged()
    except GitioError as error:
        return SetupReport([error.diagnostic])
    return report


def probe_components(
    target: Staged, setups: Mapping[Profile, ProfileSetup], rules: Iterable[str]
) -> SetupReport:
    """Confirm offline that every component's environment can be built."""
    return each_component(target, setups, rules, _probe)


def install_hooks(repo: Repository, names: Iterable[HookName]) -> SetupReport:
    """Install or update the managed hooks; a foreign hook is reported, never replaced."""
    report = SetupReport()
    for name in names:
        try:
            state = install(repo, name)
        except (HookConflictError, GitioError) as error:
            report.diagnostics.append(error.diagnostic)
            continue
        report.lines.append(f"{name} hook: {state.path}")
    return report


def hook_status(repo: Repository, required: frozenset[HookName]) -> SetupReport:
    """Report hooks that are missing, outdated, or not managed by quality."""
    report = SetupReport()
    try:
        states = [inspect(repo, name) for name in HookName]
    except (HookConflictError, GitioError) as error:
        report.diagnostics.append(error.diagnostic)
        return report
    report.diagnostics.extend(status_diagnostics(states, required=required))
    report.lines.extend(f"{state.name} hook: {state.status}" for state in states)
    return report
