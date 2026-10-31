"""`quality explain`: describe a rule and its corrective action."""

from __future__ import annotations

import argparse
import sys

from quality.catalog import build_registry
from quality.cli import Subcommands
from quality.diagnostics import ExitCode

NAME = "explain"
ACTIONS: frozenset[str] = frozenset()


def register(commands: Subcommands) -> None:
    """Add the `explain` subcommand."""
    parser = commands.add_parser(
        NAME,
        help="explain a rule ID and how to fix it",
        description="Print what a rule checks and how to fix a violation. "
        "Exit 0 when the rule exists, 2 when it is unknown.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("rule_id", nargs="?", help="rule ID, for example policy.invalid")
    group.add_argument("--list", action="store_true", help="list every rule ID")
    parser.set_defaults(handler=run)


def run(args: argparse.Namespace) -> int:
    """Print the explanation of one rule or the list of all rules."""
    registry = build_registry()
    if args.list:
        for rule in registry:
            print(f"{rule.id}\t{rule.title}")
        return int(ExitCode.OK)
    text = registry.explain(str(args.rule_id))
    if text is None:
        print(
            f"quality: unknown rule ID '{args.rule_id}'; see 'quality explain --list'",
            file=sys.stderr,
        )
        return int(ExitCode.USAGE)
    print(text)
    return int(ExitCode.OK)
