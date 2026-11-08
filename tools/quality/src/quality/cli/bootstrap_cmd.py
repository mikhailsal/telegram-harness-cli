"""`quality bootstrap`: download what offline checks need and install the managed hooks.

The launcher installs the pinned uv, CPython, and the core environment before it
runs this command. Nothing here stages, commits, or rewrites a tracked file.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from quality.catalog import build_registry
from quality.cli import Subcommands
from quality.cli.inventory_cmd import index_from
from quality.cli.local import local_setups
from quality.diagnostics import Outcome
from quality.diagnostics.render import render_text
from quality.engine.setup import SetupReport, Staged, install_hooks, provision_components
from quality.gitio import GitioError, Repository
from quality.hooks import HookName
from quality.toolchain import ManifestError, ToolchainError, cache_root, install, load_manifest
from quality.toolchain.install import INSTALL_FAILED

NAME = "bootstrap"
ACTIONS: frozenset[str] = frozenset()
# Standalone tools that checks and the core's own tests need; uv and the core are the
# launcher's part.
TOOLS = ("gitleaks", "actionlint", "osv-scanner")


def register(commands: Subcommands) -> None:
    """Add the `bootstrap` subcommand."""
    parser = commands.add_parser(
        NAME,
        help="install pinned tools and environments, then the managed Git hooks",
        description="Install the pinned standalone tools, the pinned interpreter and locked "
        "packages of every component, and the managed pre-commit hook (and pre-push with "
        "--pre-push). Downloads are verified; afterwards every check runs offline. A hook "
        "that quality did not write, or a core.hooksPath setting, is reported and left "
        "unchanged. Running it again is safe. Exit 0 when everything is ready, 2 on policy "
        "errors, 3 on download, installation, or hook conflicts.",
    )
    parser.add_argument(
        "--pre-push", action="store_true", help="also install the managed pre-push hook"
    )
    parser.set_defaults(handler=run_bootstrap)


def install_tools(names: tuple[str, ...]) -> SetupReport:
    """Install the pinned standalone tools into the cache."""
    report = SetupReport()
    try:
        manifest = load_manifest()
    except ManifestError as error:
        report.diagnostics.append(INSTALL_FAILED.diagnostic(f"toolchain manifest: {error}"))
        return report
    for name in names:
        tool = manifest.tools[name]
        try:
            path = install(name, tool, cache_root())
        except ToolchainError as error:
            report.diagnostics.append(error.diagnostic)
            continue
        report.lines.append(f"{name} {tool.version}: {path}")
    return report


def _environments(repo: Repository) -> SetupReport:
    try:
        setups = local_setups()
    except ToolchainError as error:
        return SetupReport([error.diagnostic])
    target = Staged(repo, index_from(os.environ, Path.cwd()))
    return provision_components(target, setups, (rule.id for rule in build_registry()))


def bootstrap(*, pre_push: bool) -> SetupReport:
    """Install tools, environments, and hooks; collect every finding."""
    report = install_tools(TOOLS)
    try:
        repo = Repository.discover(Path.cwd(), os.environ)
    except GitioError as error:
        report.diagnostics.append(error.diagnostic)
        return report
    report.merge(_environments(repo))
    hooks = (HookName.PRE_COMMIT, HookName.PRE_PUSH) if pre_push else (HookName.PRE_COMMIT,)
    report.merge(install_hooks(repo, hooks))
    return report


def run_bootstrap(args: argparse.Namespace) -> int:
    """Prepare this working tree for offline checks."""
    report = bootstrap(pre_push=args.pre_push)
    outcome = Outcome.of(report.diagnostics)
    sys.stdout.write("".join(f"{line}\n" for line in report.lines) + render_text(outcome))
    return int(outcome.exit_code())
