"""`quality test`: the declared suites of the affected or named components."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable

from quality.catalog import build_registry
from quality.cli import Subcommands
from quality.cli.local import local_profiles
from quality.cli.verdict import (
    candidate_of,
    coverage_json,
    coverage_text,
    scenarios_json,
    scenarios_text,
)
from quality.diagnostics import Outcome
from quality.diagnostics.render import outcome_to_json, render_text
from quality.engine import SuiteReport, SuiteSettings, run_suites
from quality.gitio import GitioError
from quality.testing import Suite
from quality.toolchain import ToolchainError

NAME = "test"
ACTIONS: frozenset[str] = frozenset()


def register(commands: Subcommands) -> None:
    """Add the `test` subcommand."""
    parser = commands.add_parser(
        NAME,
        help="run declared test suites over the staged candidate or a revision",
        description="Materialize the candidate (the index by default, or --rev) in isolation "
        "and run the selected suites (the unit suites, with their coverage floors, by "
        "default) of the components the change affects (every active component with --all, "
        "or the components named with --component), then prove the scenarios those suites "
        "map. Exit 0 on pass, 1 on failures, 2 on usage or policy errors, 3 on environment "
        "errors.",
    )
    parser.add_argument("--rev", help="test a commit instead of the index")
    parser.add_argument(
        "--suite",
        action="append",
        choices=[str(suite) for suite in Suite],
        help="a suite to run (repeatable; default unit)",
    )
    parser.add_argument(
        "--component", action="append", default=[], help="test this component (repeatable)"
    )
    parser.add_argument(
        "--all", action="store_true", dest="everything", help="test every active component"
    )
    parser.add_argument("--json", action="store_true", help="print a JSON report")
    parser.set_defaults(handler=run_test, judged=True)


def suites_of(args: argparse.Namespace) -> tuple[Suite, ...]:
    """Return the requested suites in a stable order, unit by default."""
    names: list[str] = args.suite or [str(Suite.UNIT)]
    return tuple(suite for suite in Suite if str(suite) in names)


def suite_report(args: argparse.Namespace, settings: Callable[[], SuiteSettings]) -> SuiteReport:
    """Run the suites, turning repository and toolchain failures into a report."""
    try:
        chosen = settings()
        repo, candidate = candidate_of(args)
        return run_suites(repo, candidate, chosen)
    except (GitioError, ToolchainError) as error:
        return SuiteReport((error.diagnostic,))


def write_report(report: SuiteReport, *, command: str, json: bool) -> int:
    """Print the report as text or JSON and return its exit status."""
    outcome = Outcome.of(report.diagnostics)
    if json:
        extra = {
            "tested": list(report.tested),
            "coverage": coverage_json(report.coverage),
            "scenarios": scenarios_json(report.scenarios),
        }
        sys.stdout.write(outcome_to_json(outcome, command=command, extra=extra))
    else:
        tested = ", ".join(report.tested) or "no components"
        summary = f"tested {tested}\n"
        lines = coverage_text(report.coverage) + scenarios_text(report.scenarios)
        sys.stdout.write(render_text(outcome) + lines + summary)
    return int(outcome.exit_code())


def run_test(args: argparse.Namespace) -> int:
    """Test one candidate and report every finding."""

    def settings() -> SuiteSettings:
        return SuiteSettings(
            runners=local_profiles(),
            rules={rule.id: rule for rule in build_registry()},
            everything=args.everything,
            suites=suites_of(args),
            components=frozenset(args.component),
            required=args.suite is not None,
        )

    return write_report(suite_report(args, settings), command=NAME, json=args.json)
