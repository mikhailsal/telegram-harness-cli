"""`quality fix`: apply safe native fixes to the working tree; nothing is staged."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from quality.catalog import build_registry
from quality.cli import Subcommands
from quality.cli.local import local_fixers
from quality.diagnostics import Outcome
from quality.diagnostics.render import outcome_to_json, render_text
from quality.engine.fixes import FixReport, fix_worktree
from quality.gitio import GitioError, Repository
from quality.toolchain import ToolchainError

NAME = "fix"
ACTIONS: frozenset[str] = frozenset()


def register(commands: Subcommands) -> None:
    """Add the `fix` subcommand."""
    parser = commands.add_parser(
        NAME,
        help="apply safe formatter and lint fixes to the working tree",
        description="Format and apply the native tools' safe fixes to the source and test "
        "files of every active component in the working tree (tracked and untracked, not "
        "ignored), then report what remains. A dormant helper or example is fixed only when "
        "the working tree changes it. The index is never touched: review and stage the result "
        "yourself. Exit 0 when nothing remains, 1 when findings remain, 3 on environment "
        "errors.",
    )
    parser.add_argument("--json", action="store_true", help="print a JSON report")
    parser.set_defaults(handler=run_fix)


def _report() -> FixReport:
    try:
        fixers = local_fixers()
        repo = Repository.discover(Path.cwd(), os.environ)
        return fix_worktree(repo, fixers, (rule.id for rule in build_registry()))
    except (GitioError, ToolchainError) as error:
        return FixReport((error.diagnostic,))


def run_fix(args: argparse.Namespace) -> int:
    """Fix the working tree and report what remains."""
    report = _report()
    outcome = Outcome.of(report.diagnostics)
    if args.json:
        extra = {"fixed": list(report.fixed)}
        sys.stdout.write(outcome_to_json(outcome, command=NAME, extra=extra))
    else:
        fixed = ", ".join(report.fixed) or "no components"
        sys.stdout.write(render_text(outcome) + f"fixed {fixed}; nothing was staged\n")
    return int(outcome.exit_code())
