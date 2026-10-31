"""`quality scenarios`: validate the scenario manifests and list what they require."""

from __future__ import annotations

import argparse
import sys

from quality.catalog import build_registry
from quality.cli import Subcommands
from quality.cli.verdict import candidate_of
from quality.diagnostics import Diagnostic, Outcome
from quality.diagnostics.render import outcome_to_json, printable, render_text
from quality.engine.scenarios import load_manifests
from quality.engine.scope import candidate_scope, checked_policy
from quality.gitio import Candidate, GitioError, Repository, materialize
from quality.scenarios.suggest import suggestions
from quality.scenarios.validate import Subject

NAME = "scenarios"
ACTIONS: frozenset[str] = frozenset()


def register(commands: Subcommands) -> None:
    """Add the `scenarios` subcommand."""
    parser = commands.add_parser(
        NAME,
        help="validate the scenario manifests and list the scenarios they require",
        description="Materialize the candidate (the index, or --rev) in isolation, check "
        "every active component's scenario manifest without running tests, list its "
        "scenarios, and hint at capabilities a component may have but does not declare. "
        "Only `check` and `test` prove scenarios. Exit 0 when the manifests are valid, "
        "1 on violations, 2 on usage or policy errors, 3 on Git errors.",
    )
    parser.add_argument("--rev", help="inspect a commit instead of the index")
    parser.add_argument("--json", action="store_true", help="print a JSON report")
    parser.set_defaults(handler=run_scenarios)


def _inspect(repo: Repository, candidate: Candidate) -> tuple[list[Diagnostic], list[Subject]]:
    with materialize(repo, candidate.tree) as snapshot:
        policy = checked_policy(snapshot.root, build_registry().ids())
        if isinstance(policy, tuple):
            return list(policy), []
        scope = candidate_scope(repo, candidate, snapshot, policy, everything=True)
        manifests = load_manifests(repo, candidate.base_tree, scope)
        found = list(manifests.diagnostics)
        for component in scope.active:
            prefix = "" if component.root == "." else component.root + "/"
            files = [
                record.path.removeprefix(prefix)
                for record in scope.inventory.files
                if record.component == component.id
            ]
            found.extend(suggestions(component, snapshot.root / component.root, files))
    candidate.verify_unchanged()
    return found, list(manifests.subjects.values())


def _rows(subjects: list[Subject]) -> str:
    rows = [
        f"{subject.component.id:<16} {scenario.id:<28} {scenario.capability:<13} "
        f"{scenario.level:<11} {scenario.stage:<6} {printable(', '.join(scenario.tests))}\n"
        for subject in subjects
        for scenario in subject.manifest.scenarios
    ]
    return "".join(rows)


def _json(subjects: list[Subject]) -> list[dict[str, object]]:
    return [
        {
            "component": subject.component.id,
            "manifest": subject.path,
            "id": scenario.id,
            "capability": str(scenario.capability),
            "level": str(scenario.level),
            "stage": str(scenario.stage),
            "requirements": list(scenario.requirements),
            "operations": list(scenario.operations),
            "tests": list(scenario.tests),
        }
        for subject in subjects
        for scenario in subject.manifest.scenarios
    ]


def run_scenarios(args: argparse.Namespace) -> int:
    """Check and list the scenarios of one candidate."""
    try:
        repo, candidate = candidate_of(args)
        diagnostics, subjects = _inspect(repo, candidate)
    except GitioError as error:
        diagnostics, subjects = [error.diagnostic], []
    outcome = Outcome.of(diagnostics)
    if args.json:
        extra = {"scenarios": _json(subjects)}
        sys.stdout.write(outcome_to_json(outcome, command=NAME, extra=extra))
    else:
        sys.stdout.write(_rows(subjects) + render_text(outcome))
    return int(outcome.exit_code())
