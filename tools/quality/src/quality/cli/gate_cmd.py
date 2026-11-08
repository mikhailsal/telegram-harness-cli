"""`quality gate`: the CI-independent gate over explicit revisions (decision 0015)."""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from quality.catalog import build_registry
from quality.ci import Lane
from quality.cli import Subcommands
from quality.cli.inventory_cmd import index_from
from quality.cli.local import local_profiles, local_tools
from quality.cli.verdict import candidate_of, report_extra, report_text
from quality.diagnostics import Outcome
from quality.diagnostics.render import outcome_to_json, render_text
from quality.engine import Report, Settings, check_candidate
from quality.engine.gate import GateError, Revisions, gate_candidate, usage_error
from quality.gitio import Candidate, GitioError, Repository
from quality.policy.scenarios import Stage
from quality.reports import ReportsError, prepare_reports
from quality.rules.regressions import baseline_regressions
from quality.toolchain import ToolchainError

NAME = "gate"
ACTIONS: frozenset[str] = frozenset()
REPORT = "report.json"


def register(commands: Subcommands) -> None:
    """Add the `gate` subcommand."""
    parser = commands.add_parser(
        NAME,
        help="run every gate over explicit revisions, as CI does",
        description="Check every active component of one candidate without CI variables. "
        "--stage commit checks the index (or --index), or a pushed range with --base and "
        "--head; --stage merge checks the tested tree of a pull request (--tested, the head "
        "by default) or of a merge-queue entry (--queue) against --base and --head. A range "
        "also checks every blob of its new commits; missing history fails. --lane checks one "
        "part, as a CI job does: main the common gates and every component that does not run "
        "in a browser, browser the browser components. --reports writes report.json, JUnit, "
        "LCOV, Cobertura, and native reports into a new or empty directory. Exit 0 on pass, "
        "1 on violations, 2 on usage or policy errors, 3 on environment errors.",
    )
    parser.add_argument(
        "--stage", choices=[str(stage) for stage in Stage], required=True, help="gate stage"
    )
    parser.add_argument("--base", help="base-branch revision, or the previous tip of a push")
    parser.add_argument("--head", help="source-head revision, or the pushed tip")
    parser.add_argument("--tested", help="tested merge revision of a pull request (merge stage)")
    parser.add_argument(
        "--queue", action="store_true", help="the head is a merge-queue entry built on the base"
    )
    parser.add_argument("--index", type=Path, help="alternate index file (commit stage)")
    parser.add_argument(
        "--lane", choices=[str(lane) for lane in Lane], help="check one lane, as a CI job does"
    )
    parser.add_argument("--reports", type=Path, help="new or empty directory for the reports")
    parser.add_argument("--baseline", type=Path, help="earlier report.json to report regressions")
    parser.add_argument("--json", action="store_true", help="print a JSON report")
    parser.set_defaults(handler=run_gate, judged=True, select=select)


def revisions_of(args: argparse.Namespace) -> Revisions:
    """Return the revisions the arguments name; the index defaults to the hook's selection."""
    index: Path | None = args.index or index_from(os.environ, Path.cwd())
    return Revisions(Stage(args.stage), args.base, args.head, args.tested, args.queue, index)


def select(repo: Repository, args: argparse.Namespace) -> Candidate:
    """Return the gate's candidate, for the verdict and for the provenance check."""
    return gate_candidate(repo, revisions_of(args))


def _prepared(args: argparse.Namespace) -> Path | None:
    reports: Path | None = args.reports
    if reports is None:
        return None
    try:
        return prepare_reports(reports)
    except ReportsError as error:
        raise usage_error(str(error)) from error


def _report(args: argparse.Namespace, reports: Path | None) -> tuple[Report, Candidate]:
    repo, candidate = candidate_of(args)
    settings = Settings(
        tools=local_tools(),
        commands=args.command_table,
        rules={rule.id: rule for rule in build_registry()},
        today=datetime.now(UTC).date(),
        profiles=local_profiles(),
        everything=True,
        stage=Stage(args.stage),
        reports=reports,
        lane=Lane(args.lane) if args.lane is not None else None,
    )
    report = check_candidate(repo, candidate, settings)
    if args.baseline is not None:
        regressed = baseline_regressions(args.baseline, report.coverage)
        report = replace(report, diagnostics=(*report.diagnostics, *regressed))
    return report, candidate


def _identity(args: argparse.Namespace, candidate: Candidate | None) -> dict[str, object]:
    revisions = {key: getattr(args, key) for key in ("base", "head", "tested")}
    tree = None if candidate is None else candidate.tree
    base_tree = None if candidate is None else candidate.base_tree
    shown = {"stage": args.stage, "lane": args.lane, **revisions, "queue": args.queue}
    return {**shown, "tree": tree, "base_tree": base_tree}


def run_gate(args: argparse.Namespace) -> int:
    """Check the candidate the revisions name, then print and record the report.

    report.json is written only into a directory this run prepared, so a
    refused directory keeps its earlier reports.
    """
    candidate, reports = None, None
    try:
        reports = _prepared(args)
        report, candidate = _report(args, reports)
    except (GateError, GitioError, ToolchainError) as error:
        report = Report((error.diagnostic,))
    outcome = Outcome.of(report.diagnostics)
    extra = {"candidate": _identity(args, candidate), **report_extra(report)}
    document = outcome_to_json(outcome, command=NAME, extra=extra)
    if reports is not None:
        (reports / REPORT).write_text(document, encoding="utf-8")
    sys.stdout.write(document if args.json else render_text(outcome) + report_text(report))
    return int(outcome.exit_code())
