"""`quality test`: the suites and coverage floors of the components a candidate affects.

The suites run as in `quality check` (see `engine.runs`, R06); this module
selects the components and suites a test command names.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from quality.diagnostics import Diagnostic, Kind, Rule
from quality.engine.profiles import ProfileTests
from quality.engine.runs import suite_findings
from quality.engine.scenarios import load_manifests, reconciled
from quality.engine.scope import Scope, candidate_scope, checked_policy, prepare_components
from quality.gitio import Candidate, Repository, materialize
from quality.policy import Component, Profile, Role
from quality.rules import coverage, quarantine
from quality.scenarios.reconcile import ScenarioSummary
from quality.testing import Suite, suite_roots

UNKNOWN = Rule(
    id="tests.unknown-component",
    title="A requested component is not an active component",
    requirement="R06",
    explanation=(
        "`quality test --component` and `quality self-test` run the suites of named "
        "components. A name that the policy does not declare, or whose component is not "
        "active in the candidate, would test nothing, so it is a usage error."
    ),
    remedy="Name an active component from quality.toml; `quality inventory` lists them.",
    kind=Kind.USAGE,
)
UNDECLARED = Rule(
    id="tests.undeclared",
    title="No selected component declares the requested suite",
    requirement="R06",
    explanation=(
        "A suite was requested explicitly, but none of the selected components declares "
        "test roots for it, so nothing would run."
    ),
    remedy="Declare the suite's roots under the component's `tests` table, or omit it.",
    kind=Kind.USAGE,
)
RULES = (UNKNOWN, UNDECLARED)


@dataclass(frozen=True, slots=True)
class SuiteSettings:
    """Runners per profile, the known rules, and which components and suites to run.

    Named `components`, or every active component with `role`, replace the affected
    selection; `required` suites must be declared by at least one selected component.
    """

    runners: Mapping[Profile, ProfileTests]
    rules: Mapping[str, Rule]
    everything: bool = False
    suites: tuple[Suite, ...] = (Suite.UNIT,)
    components: frozenset[str] = frozenset()
    role: Role | None = None
    required: bool = False
    today: date = field(default_factory=lambda: datetime.now(UTC).date())


@dataclass(frozen=True, slots=True)
class SuiteReport:
    """The result of testing one candidate."""

    diagnostics: tuple[Diagnostic, ...]
    tested: tuple[str, ...] = ()
    coverage: tuple[coverage.ComponentCoverage, ...] = ()
    scenarios: tuple[ScenarioSummary, ...] = ()


type Selection = tuple[tuple[Component, ...], list[Diagnostic]]


def declares(component: Component, suites: Iterable[Suite]) -> bool:
    """Whether the component declares roots for any of the suites."""
    return any(suite_roots(component, suite) for suite in suites)


def _by_name(scope: Scope, names: frozenset[str]) -> Selection:
    active = {component.id for component in scope.active}
    unknown = [
        UNKNOWN.diagnostic(f"'{name}' is not an active component")
        for name in sorted(names - active)
    ]
    return tuple(c for c in scope.active if c.id in names), unknown


def _by_role(scope: Scope, role: Role) -> Selection:
    selected = tuple(c for c in scope.active if c.role is role)
    missing = [] if selected else [UNKNOWN.diagnostic(f"no active component has the {role} role")]
    return selected, missing


def _undeclared(selected: tuple[Component, ...], suites: Iterable[Suite]) -> list[Diagnostic]:
    names = ", ".join(component.id for component in selected)
    return [
        UNDECLARED.diagnostic(f"no {suite} suite is declared by {names}")
        for suite in suites
        if not any(declares(component, (suite,)) for component in selected)
    ]


def named_components(scope: Scope, settings: SuiteSettings) -> Selection:
    """Resolve the components to test and check that the requested suites exist."""
    selected = scope.selected
    found: list[Diagnostic] = []
    if settings.components:
        selected, found = _by_name(scope, settings.components)
    if settings.role is not None:
        selected, missing = _by_role(scope, settings.role)
        found.extend(missing)
    if settings.required and selected:
        found.extend(_undeclared(selected, settings.suites))
    if Suite.UNIT not in settings.suites:
        # Every component needs a unit suite; the other levels concern only those declaring them.
        selected = tuple(c for c in selected if declares(c, settings.suites))
    return selected, found


def _inspect(repo: Repository, candidate: Candidate, settings: SuiteSettings) -> SuiteReport:
    with materialize(repo, candidate.tree) as snapshot:
        policy = checked_policy(snapshot.root, settings.rules)
        if isinstance(policy, tuple):
            return SuiteReport(policy)
        scope = candidate_scope(repo, candidate, snapshot, policy, everything=settings.everything)
        selected, problems = named_components(scope, settings)
        if problems:
            return SuiteReport(tuple(problems))
        ready, found = prepare_components(settings.runners, selected, snapshot)
        held, expired = quarantine.current(policy.quarantine, settings.today)
        manifests = load_manifests(repo, candidate.base_tree, scope)
        findings = suite_findings(scope, ready, lambda _: settings.suites, held)
        proved, scenarios = reconciled(manifests, findings.ran, None, held)
    tested = tuple(component.id for component, _ in ready)
    static = [d for d in manifests.diagnostics if d.component in tested]
    diagnostics = (*found, *expired, *static, *findings.diagnostics, *proved)
    return SuiteReport(diagnostics, tested, tuple(findings.coverage), tuple(scenarios))


def run_suites(repo: Repository, candidate: Candidate, settings: SuiteSettings) -> SuiteReport:
    """Test a candidate and confirm it did not change while its suites ran."""
    report = _inspect(repo, candidate, settings)
    candidate.verify_unchanged()
    return report
