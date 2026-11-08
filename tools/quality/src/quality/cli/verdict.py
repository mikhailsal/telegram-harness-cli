"""What the commands that judge a candidate share: the candidate, coverage, and scenarios."""

from __future__ import annotations

import argparse
import os
from collections.abc import Callable
from pathlib import Path
from typing import cast

from quality.cli.inventory_cmd import index_from
from quality.engine import Report
from quality.gitio import Candidate, Repository, revision, staged
from quality.rules.coverage import ComponentCoverage
from quality.scenarios.reconcile import ScenarioSummary

type Select = Callable[[Repository, argparse.Namespace], Candidate]


def candidate_of(args: argparse.Namespace) -> tuple[Repository, Candidate]:
    """Return the repository and the candidate: the command's own, `--rev`, or the index.

    A command whose candidate comes from other arguments sets `select`; the
    others take `--rev` or the index a hook selected.
    """
    repo = Repository.discover(Path.cwd(), os.environ)
    select = cast(Select | None, getattr(args, "select", None))
    if select is not None:
        return repo, select(repo, args)
    rev: str | None = args.rev
    if rev is not None:
        return repo, revision(repo, rev)
    return repo, staged(repo, index_from(os.environ, Path.cwd()))


def report_extra(report: Report) -> dict[str, object]:
    """Return what a check report adds to the JSON document of its outcome."""
    return {
        "files": report.files,
        "excepted": report.excepted,
        "tested": list(report.tested),
        "coverage": coverage_json(report.coverage),
        "scenarios": scenarios_json(report.scenarios),
    }


def report_text(report: Report) -> str:
    """Return the coverage, scenario, and summary lines that follow a check's findings."""
    summary = f"checked {report.files} files; {report.excepted} findings covered by exceptions\n"
    return coverage_text(report.coverage) + scenarios_text(report.scenarios) + summary


def coverage_text(summaries: tuple[ComponentCoverage, ...]) -> str:
    """Describe the unit coverage of each tested component, one line each."""
    lines: list[str] = []
    for summary in summaries:
        branch = summary.branch.describe() if summary.branch is not None else "not measured"
        lines.append(
            f"unit coverage of {summary.component}: line {summary.line.describe()}, "
            f"branch {branch}, changed lines {summary.changed.describe()}\n"
        )
    return "".join(lines)


def coverage_json(summaries: tuple[ComponentCoverage, ...]) -> list[dict[str, object]]:
    """Return the unit coverage of each tested component as JSON-ready counters."""
    found: list[dict[str, object]] = []
    for summary in summaries:
        counters = {"line": summary.line, "branch": summary.branch, "changed": summary.changed}
        found.append(
            {
                "component": summary.component,
                **{
                    name: None if c is None else {"covered": c.covered, "total": c.total}
                    for name, c in counters.items()
                },
            }
        )
    return found


def scenarios_text(summaries: tuple[ScenarioSummary, ...]) -> str:
    """Describe how many scenarios of each reconciled component this run proved."""
    lines: list[str] = []
    for summary in summaries:
        line = f"scenarios of {summary.component}: {summary.passed} of {summary.required} proved"
        line += f", {summary.suspended} suspended" if summary.suspended else ""
        line += f", {summary.deferred} not required by this run" if summary.deferred else ""
        if summary.boundaries:
            line += f"; tested boundaries: {'; '.join(summary.boundaries)}"
        lines.append(line + "\n")
    return "".join(lines)


def scenarios_json(summaries: tuple[ScenarioSummary, ...]) -> list[dict[str, object]]:
    """Return the scenario counts of each reconciled component."""
    return [
        {
            "component": summary.component,
            "required": summary.required,
            "proved": summary.passed,
            "suspended": summary.suspended,
            "deferred": summary.deferred,
            "boundaries": list(summary.boundaries),
        }
        for summary in summaries
    ]
