"""Argument parsing and dispatch for the `quality` command."""

from __future__ import annotations

import argparse
import sys
import traceback
from collections.abc import Callable, Sequence
from typing import cast

from quality import __version__
from quality.cli import (
    audit_cmd,
    bootstrap_cmd,
    check_cmd,
    ci_cmd,
    component_cmd,
    doctor_cmd,
    explain_cmd,
    fix_cmd,
    gate_cmd,
    inventory_cmd,
    links_cmd,
    policy_cmd,
    scenarios_cmd,
    selftest_cmd,
    suites_cmd,
    template_cmd,
    tools_cmd,
)
from quality.cli.provenance import judge
from quality.diagnostics import ExitCode

type Handler = Callable[[argparse.Namespace], int]

DESCRIPTION = """\
Quality gate for Git repositories. The command-line interface decides pass or
fail; Git hooks and CI only call it. Exit codes: 0 pass, 1 violations,
2 usage or policy error, 3 environment or tool error, 4 internal error.
"""

COMMANDS = (
    audit_cmd,
    bootstrap_cmd,
    check_cmd,
    ci_cmd,
    component_cmd,
    doctor_cmd,
    explain_cmd,
    fix_cmd,
    gate_cmd,
    inventory_cmd,
    links_cmd,
    policy_cmd,
    scenarios_cmd,
    selftest_cmd,
    suites_cmd,
    template_cmd,
    tools_cmd,
)


def command_table() -> dict[str, frozenset[str]]:
    """Return each command with its actions, so documentation can be checked against it."""
    return {module.NAME: module.ACTIONS for module in COMMANDS}


def build_parser() -> argparse.ArgumentParser:
    """Create the top-level parser with every implemented subcommand."""
    parser = argparse.ArgumentParser(
        prog="quality",
        description=DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"quality {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="<command>", required=True)
    for module in COMMANDS:
        module.register(commands)
    parser.set_defaults(command_table=command_table())
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return a deterministic exit code."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(arguments)
    handler = cast(Handler, args.handler)
    try:
        # Verdict commands first make sure the candidate's own core judges it.
        verdict = judge(args, arguments) if getattr(args, "judged", False) else None
        return int(handler(args)) if verdict is None else verdict
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    except Exception:  # noqa: BLE001 - reported as an internal error, never as success
        traceback.print_exc(file=sys.stderr)
        print("quality: internal error (exit 4); please report it", file=sys.stderr)
        return int(ExitCode.INTERNAL)
