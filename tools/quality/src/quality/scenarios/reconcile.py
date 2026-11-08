"""Reconcile mandatory scenarios with the results of the suites that ran (R08.3).

A scenario is proved only by this run: every test it names must be among the
cases of its suite's fresh results, and every case it selects must have
passed. A skip, an expected failure, an absent case, a prefix that selects
nothing, a suite without results, and a quarantined case all leave it unmet.
"""

from __future__ import annotations

import difflib
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass

from quality.diagnostics import Diagnostic, Location, Rule
from quality.policy import Quarantine, Suite
from quality.policy.scenarios import Scenario, ScenarioManifest
from quality.scenarios.catalog import EVIDENCE_NAMES, requirement
from quality.scenarios.rules import (
    ABSENT,
    AMBIGUOUS,
    EVIDENCE,
    FAILED,
    QUARANTINED,
    SKIPPED,
    SUSPENDED,
    UNVERIFIED,
)
from quality.testing import CaseResult, Outcome, SuiteResults

PROBLEMS = {
    Outcome.FAILED: (FAILED, "failed"),
    Outcome.ERROR: (FAILED, "raised an error"),
    Outcome.SKIPPED: (SKIPPED, "was skipped"),
    Outcome.EXPECTED_FAILURE: (SKIPPED, "was expected to fail"),
}
SIMILAR = 3

# The results of the suites one component ran; None when a suite left no usable results.
type Ran = Mapping[Suite, SuiteResults | None]


@dataclass(frozen=True, slots=True)
class ScenarioSummary:
    """How many of a component's scenarios this run had to prove, and how many it proved."""

    component: str
    required: int
    passed: int
    suspended: int = 0
    deferred: int = 0
    boundaries: tuple[str, ...] = ()


def selected(selector: str, names: Sequence[str]) -> list[str]:
    """Return the case names a test entry selects: one exact name, or every name a prefix starts."""
    if selector.endswith("*"):
        return [name for name in names if name.startswith(selector[:-1])]
    return [name for name in names if name == selector]


def _absent(selector: str, names: Sequence[str], level: Suite) -> str:
    if selector.endswith("*"):
        return f"'{selector}' selects no case of the {level} suite"
    similar = difflib.get_close_matches(selector, names, n=SIMILAR)
    hint = "; similar: " + ", ".join(f"'{name}'" for name in similar) if similar else ""
    return f"the {level} suite has no case '{selector}'{hint}"


def _cases(scenario: Scenario, results: SuiteResults) -> Iterator[tuple[Rule, str] | str]:
    """Yield the mapping's problems, then the names of the cases it selects."""
    names = [case.name for case in results.cases]
    for selector in scenario.tests:
        chosen = selected(selector, names)
        if not chosen:
            yield ABSENT, _absent(selector, names, scenario.level)
        elif len(chosen) > 1 and not selector.endswith("*"):
            yield AMBIGUOUS, f"'{selector}' names {len(chosen)} cases of the {scenario.level} suite"
        yield from chosen


def needed_evidence(scenario: Scenario) -> tuple[str, ...]:
    """Return the evidence the scenario's requirements need from each case beside a pass."""
    kinds: dict[str, None] = {}
    for identifier in scenario.requirements:
        found = requirement(identifier)
        if found is not None and found[1].evidence is not None:
            kinds[found[1].evidence] = None
    return tuple(kinds)


@dataclass(slots=True)
class _Verdict:
    problems: dict[Rule, list[str]]
    suspended: list[str]

    def add(self, rule: Rule, message: str) -> None:
        self.problems.setdefault(rule, []).append(message)


def _judge_case(
    verdict: _Verdict,
    scenario: str,
    case: CaseResult,
    held: Quarantine | None,
    needed: Sequence[str],
) -> None:
    """Record why one selected case does not prove the scenario, or that it is quarantined."""
    if held is not None and scenario in held.scenarios:
        verdict.suspended.append(f"'{case.name}' is quarantined until {held.expires}")
    elif held is not None:
        verdict.add(
            QUARANTINED, f"'{case.name}' is quarantined, and its entry does not name this scenario"
        )
    elif case.outcome in PROBLEMS:
        rule, verb = PROBLEMS[case.outcome]
        verdict.add(rule, f"'{case.name}' {verb}")
    else:
        for kind in (kind for kind in needed if kind not in case.evidence):
            verdict.add(EVIDENCE, f"'{case.name}' passed without {EVIDENCE_NAMES[kind]}")


def _judge(scenario: Scenario, results: SuiteResults, entries: Sequence[Quarantine]) -> _Verdict:
    verdict = _Verdict({}, [])
    cases = {case.name: case for case in results.cases}
    needed = needed_evidence(scenario)
    held = {entry.test: entry for entry in entries}
    for item in dict.fromkeys(_cases(scenario, results)):
        if isinstance(item, str):
            _judge_case(verdict, scenario.id, cases[item], held.get(item), needed)
        else:
            verdict.add(*item)
    return verdict


def _findings(
    scenario: Scenario, verdict: _Verdict, location: Location, component: str
) -> list[Diagnostic]:
    head = f"scenario '{scenario.id}' ({scenario.capability}, {scenario.level})"
    if verdict.problems:
        return [
            rule.diagnostic(f"{head}: {'; '.join(details)}", location=location, component=component)
            for rule, details in verdict.problems.items()
        ]
    if verdict.suspended:
        message = f"{head} is suspended: {'; '.join(verdict.suspended)}"
        return [SUSPENDED.diagnostic(message, location=location, component=component)]
    return []


def _held(quarantine: Sequence[Quarantine], component: str, level: Suite) -> list[Quarantine]:
    return [entry for entry in quarantine if entry.component == component and entry.suite is level]


def _boundaries(scenarios: Sequence[Scenario]) -> tuple[str, ...]:
    """Return the distinct boundaries the scenarios name, in their order."""
    return tuple(dict.fromkeys(s.boundary for s in scenarios if s.boundary is not None))


def reconcile(
    manifest: ScenarioManifest,
    path: str,
    required: Callable[[Scenario], bool],
    ran: Ran,
    quarantine: Sequence[Quarantine],
) -> tuple[list[Diagnostic], ScenarioSummary]:
    """Prove the required scenarios of one component with the results of its suites."""
    component, location = manifest.component, Location(path)
    found: list[Diagnostic] = []
    needed = [scenario for scenario in manifest.scenarios if required(scenario)]
    passed = suspended = 0
    for scenario in needed:
        results = ran.get(scenario.level)
        if results is None:
            message = (
                f"scenario '{scenario.id}' ({scenario.capability}, {scenario.level}): "
                f"the {scenario.level} suite left no results to prove it"
            )
            found.append(UNVERIFIED.diagnostic(message, location=location, component=component))
            continue
        verdict = _judge(scenario, results, _held(quarantine, component, scenario.level))
        found.extend(_findings(scenario, verdict, location, component))
        passed += not verdict.problems and not verdict.suspended
        suspended += not verdict.problems and bool(verdict.suspended)
    deferred = len(manifest.scenarios) - len(needed)
    boundaries = _boundaries(needed)
    summary = ScenarioSummary(component, len(needed), passed, suspended, deferred, boundaries)
    return found, summary
