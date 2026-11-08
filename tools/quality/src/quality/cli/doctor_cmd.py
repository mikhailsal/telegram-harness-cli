"""`quality doctor`: verify, offline and read-only, that checks can run here."""

from __future__ import annotations

import argparse
import os
import platform
import sys
from collections.abc import Callable
from pathlib import Path

from quality.catalog import build_registry
from quality.cli import Subcommands
from quality.cli.inventory_cmd import index_from
from quality.cli.local import core_tool, local_scanner, local_setups, local_tools, local_uv
from quality.diagnostics import Outcome
from quality.diagnostics.render import outcome_to_json, render_text
from quality.engine.setup import SetupReport, Staged, hook_status, probe_components
from quality.gitio import GitioError, Repository, version_findings
from quality.hooks import HookName
from quality.toolchain import ToolchainError

NAME = "doctor"
ACTIONS: frozenset[str] = frozenset()
REQUIRED_HOOKS = frozenset({HookName.PRE_COMMIT})


def register(commands: Subcommands) -> None:
    """Add the `doctor` subcommand."""
    parser = commands.add_parser(
        NAME,
        help="verify the local toolchain, environments, and hooks without changing them",
        description="Check the Git version, the pinned tools, an offline build of every "
        "component environment, and the managed hooks. Nothing is downloaded or modified. "
        "Exit 0 when checks can run, 2 on policy errors, 3 when something is missing.",
    )
    parser.add_argument("--json", action="store_true", help="print a JSON report")
    parser.set_defaults(handler=run_doctor)


def toolchain() -> SetupReport:
    """Locate the pinned tools a check runs."""
    report = SetupReport([], [f"python {platform.python_version()}: {sys.executable}"])
    probes: dict[str, Callable[[], Path]] = {
        "uv": lambda: local_uv().executable,
        "gitleaks": lambda: local_tools().gitleaks,
        "actionlint": lambda: local_tools().actionlint,
        "osv-scanner": local_scanner,
        "ruff": lambda: core_tool("ruff"),
        "pyright": lambda: core_tool("pyright"),
    }
    for name, locate in probes.items():
        try:
            report.lines.append(f"{name}: {locate()}")
        except ToolchainError as error:
            report.diagnostics.append(error.diagnostic)
    return report


def _repository(report: SetupReport) -> Repository | None:
    try:
        repo = Repository.discover(Path.cwd(), os.environ)
        version, problems = version_findings(repo)
    except GitioError as error:
        report.diagnostics.append(error.diagnostic)
        return None
    report.lines.append(f"git {version}")
    report.diagnostics.extend(problems)
    return repo


def diagnose() -> SetupReport:
    """Run every read-only check."""
    report = SetupReport()
    repo = _repository(report)
    report.merge(toolchain())
    if repo is None:
        return report
    try:
        setups = local_setups()
    except ToolchainError as error:
        report.diagnostics.append(error.diagnostic)
    else:
        rules = (rule.id for rule in build_registry())
        report.merge(
            probe_components(Staged(repo, index_from(os.environ, Path.cwd())), setups, rules)
        )
    report.merge(hook_status(repo, REQUIRED_HOOKS))
    return report


def run_doctor(args: argparse.Namespace) -> int:
    """Report whether checks can run in this working tree."""
    report = diagnose()
    outcome = Outcome.of(report.diagnostics)
    if args.json:
        extra = {"checks": report.lines}
        sys.stdout.write(outcome_to_json(outcome, command=NAME, extra=extra))
    else:
        sys.stdout.write("".join(f"{line}\n" for line in report.lines) + render_text(outcome))
    return int(outcome.exit_code())
