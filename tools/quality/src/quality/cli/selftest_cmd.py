"""`quality self-test`: the suites of the quality tooling itself."""

from __future__ import annotations

import argparse

from quality.catalog import build_registry
from quality.cli import Subcommands
from quality.cli.local import local_profiles
from quality.cli.suites_cmd import suite_report, write_report
from quality.engine import SuiteSettings
from quality.policy import Role
from quality.testing import Suite

NAME = "self-test"
ACTIONS: frozenset[str] = frozenset()
LEVELS = {"unit": Suite.UNIT, "e2e": Suite.E2E, "acceptance": Suite.ACCEPTANCE}


def register(commands: Subcommands) -> None:
    """Add the `self-test` subcommand."""
    parser = commands.add_parser(
        NAME,
        help="run the test suites of the quality tooling components",
        description="Run the unit suites with coverage floors (--level unit, the default), "
        "the built-artifact suites (--level e2e), or the acceptance suites (--level "
        "acceptance) of every active tooling component that declares them, over "
        "the staged candidate or --rev. Acceptance suites drive real Git hooks in scratch "
        "repositories. Exit 0 on pass, 1 on failures, 2 when no tooling component declares "
        "the level, 3 on environment errors.",
    )
    parser.add_argument("--level", choices=sorted(LEVELS), default="unit", help="suite level")
    parser.add_argument("--rev", help="test a commit instead of the index")
    parser.add_argument("--json", action="store_true", help="print a JSON report")
    parser.set_defaults(handler=run_self_test, judged=True)


def run_self_test(args: argparse.Namespace) -> int:
    """Test the tooling components of one candidate."""

    def settings() -> SuiteSettings:
        return SuiteSettings(
            runners=local_profiles(),
            rules={rule.id: rule for rule in build_registry()},
            suites=(LEVELS[args.level],),
            role=Role.TOOLING,
            required=True,
        )

    return write_report(suite_report(args, settings), command=NAME, json=args.json)
