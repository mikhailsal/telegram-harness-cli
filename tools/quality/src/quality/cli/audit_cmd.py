"""`quality audit`: the dependency vulnerability gate of merge CI (decision 0015)."""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from quality.catalog import build_registry
from quality.cli import Subcommands
from quality.cli.local import local_scanner
from quality.cli.verdict import candidate_of
from quality.diagnostics import Outcome
from quality.diagnostics.render import outcome_to_json, render_text
from quality.engine.audit import AuditReport, AuditSettings, audit_candidate
from quality.gitio import GitioError
from quality.toolchain import ToolchainError

NAME = "audit"
ACTIONS: frozenset[str] = frozenset()


def register(commands: Subcommands) -> None:
    """Add the `audit` subcommand."""
    parser = commands.add_parser(
        NAME,
        help="scan the candidate's lockfiles for known vulnerabilities",
        description="Materialize the candidate (the index by default, or --rev) and match "
        "every lockfile against the OSV database with the pinned osv-scanner (install it "
        "with `quality tools install osv-scanner`). Advisories come from the osv.dev API "
        "unless --offline-db names a local database directory. The [audit] policy sets "
        "which scores block. Exit 0 on pass, 1 on blocking advisories, 2 on policy errors, "
        "3 when the scanner or its database fails.",
    )
    parser.add_argument("--rev", help="audit a commit instead of the index")
    parser.add_argument(
        "--offline-db", type=Path, help="match offline against this OSV database directory"
    )
    parser.add_argument("--json", action="store_true", help="print a JSON report")
    parser.set_defaults(handler=run_audit, judged=True)


def _report(args: argparse.Namespace) -> AuditReport:
    repo, candidate = candidate_of(args)
    database: Path | None = args.offline_db
    settings = AuditSettings(
        scanner=local_scanner(),
        rules={rule.id: rule for rule in build_registry()},
        today=datetime.now(UTC).date(),
        database=database.resolve() if database is not None else None,
    )
    return audit_candidate(repo, candidate, settings)


def run_audit(args: argparse.Namespace) -> int:
    """Audit one candidate and report every advisory."""
    try:
        report = _report(args)
    except (GitioError, ToolchainError) as error:
        report = AuditReport((error.diagnostic,))
    outcome = Outcome.of(report.diagnostics)
    if args.json:
        extra = {"lockfiles": list(report.lockfiles), "excepted": report.excepted}
        sys.stdout.write(outcome_to_json(outcome, command=NAME, extra=extra))
    else:
        scanned = ", ".join(report.lockfiles) or "none"
        summary = f"audited lockfiles: {scanned}; {report.excepted} covered by exceptions\n"
        sys.stdout.write(render_text(outcome) + summary)
    return int(outcome.exit_code())
