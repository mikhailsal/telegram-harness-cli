"""`quality links`: check external documentation links, separately from commits."""

from __future__ import annotations

import argparse
import functools
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from quality.adapters import web
from quality.catalog import build_registry
from quality.cli import Subcommands
from quality.cli.inventory_cmd import index_from
from quality.diagnostics import Outcome
from quality.diagnostics.render import outcome_to_json, render_text
from quality.engine import Report, check_links
from quality.gitio import GitioError, Repository, revision, staged

NAME = "links"
ACTIONS: frozenset[str] = frozenset()
DEFAULT_TIMEOUT = 10.0


def register(commands: Subcommands) -> None:
    """Add the `links` subcommand."""
    parser = commands.add_parser(
        NAME,
        help="probe external documentation links with timeouts",
        description="Probe every external http(s) link in the candidate's Markdown "
        "documentation. Hosts with non-public addresses are not contacted and response bodies "
        "are not read. Exit 1 only for links answered with HTTP 404 or 410; unreachable or "
        "refusing sites are warnings. This command is not part of `quality check`.",
    )
    parser.add_argument("--rev", help="check a commit instead of the index")
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"seconds per request (default {DEFAULT_TIMEOUT:g})",
    )
    parser.add_argument("--json", action="store_true", help="print a JSON report")
    parser.set_defaults(handler=run_links)


def _report(args: argparse.Namespace) -> Report:
    repo = Repository.discover(Path.cwd(), os.environ)
    candidate = (
        revision(repo, args.rev)
        if args.rev is not None
        else staged(repo, index_from(os.environ, Path.cwd()))
    )
    return check_links(
        repo,
        candidate,
        rules={rule.id: rule for rule in build_registry()},
        prober=functools.partial(web.probe, timeout=args.timeout),
        today=datetime.now(UTC).date(),
    )


def run_links(args: argparse.Namespace) -> int:
    """Probe the links and report the findings."""
    if args.timeout <= 0:
        print("quality links: --timeout must be positive", file=sys.stderr)
        return 2
    try:
        report = _report(args)
    except GitioError as error:
        report = Report((error.diagnostic,))
    outcome = Outcome.of(report.diagnostics)
    if args.json:
        extra = {"pages": report.files, "excepted": report.excepted}
        sys.stdout.write(outcome_to_json(outcome, command=NAME, extra=extra))
    else:
        sys.stdout.write(render_text(outcome) + f"checked links in {report.files} pages\n")
    return int(outcome.exit_code())
