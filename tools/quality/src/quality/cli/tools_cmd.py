"""`quality tools`: install the pinned standalone tools, or move a pin to another release."""

from __future__ import annotations

import argparse
import sys

from quality.cli import Subcommands
from quality.diagnostics import Diagnostic, Outcome
from quality.diagnostics.render import render_text
from quality.toolchain import (
    ManifestError,
    ToolchainError,
    UpdateError,
    cache_root,
    install,
    load_manifest,
    update,
)
from quality.toolchain.install import INSTALL_FAILED
from quality.toolchain.manifest import MANIFEST

NAME = "tools"
ACTIONS = frozenset({"install", "update"})
DEFAULT_TOOLS = ("gitleaks", "actionlint", "osv-scanner")


def register(commands: Subcommands) -> None:
    """Add the `tools` subcommand group."""
    parser = commands.add_parser(
        NAME,
        help="install pinned standalone tools",
        description="Install tools pinned in tools/quality/toolchains.toml into the cache. "
        "Downloads are verified against their SHA-256 checksums before anything is installed.",
    )
    actions = parser.add_subparsers(dest="action", metavar="<action>", required=True)
    add = actions.add_parser(
        "install",
        help="download, verify, and install pinned tools",
        description="Install the named tools, or the default ones (gitleaks, actionlint, and "
        "osv-scanner). Exit 0 when all are installed, 2 for unknown names, 3 when a download "
        "fails.",
    )
    add.add_argument("names", nargs="*", help="tool names from toolchains.toml")
    add.set_defaults(handler=run_install)
    move = actions.add_parser(
        "update",
        help="move a pinned tool to another release",
        description="Rewrite the tool's entry in toolchains.toml for VERSION: the artifact URLs "
        "and members get the new version, and the checksums come from the vendor's checksum "
        "file that the tool's `checksums` names. Nothing is installed or staged. Exit 0 when "
        "rewritten, 2 when the entry cannot be derived, 3 when a download fails.",
    )
    move.add_argument("name", help="a tool name from toolchains.toml")
    move.add_argument("version", help="the release to pin")
    move.set_defaults(handler=run_update)


def run_install(args: argparse.Namespace) -> int:
    """Install each requested tool and print where it is."""
    found: list[Diagnostic] = []
    try:
        manifest = load_manifest()
    except ManifestError as error:
        found.append(INSTALL_FAILED.diagnostic(str(error)))
        sys.stdout.write(render_text(Outcome.of(found)))
        return int(Outcome.of(found).exit_code())
    names: list[str] = list(args.names) or list(DEFAULT_TOOLS)
    unknown = [name for name in names if name not in manifest.tools]
    if unknown:
        print(f"unknown tools: {', '.join(unknown)}", file=sys.stderr)
        return 2
    root = cache_root()
    for name in names:
        try:
            path = install(name, manifest.tools[name], root)
        except ToolchainError as error:
            found.append(error.diagnostic)
            continue
        print(f"{name} {manifest.tools[name].version}: {path}")
    outcome = Outcome.of(found)
    if found:
        sys.stdout.write(render_text(outcome))
    return int(outcome.exit_code())


def run_update(args: argparse.Namespace) -> int:
    """Rewrite one tool's pins and print the new artifacts."""
    try:
        updated = update(MANIFEST.read_text(encoding="utf-8"), args.name, args.version)
    except UpdateError as error:
        outcome = Outcome.of([error.diagnostic])
        sys.stdout.write(render_text(outcome))
        return int(outcome.exit_code())
    MANIFEST.write_text(updated.text, encoding="utf-8")
    print(f"{args.name} {updated.old.version} -> {updated.new.version} in {MANIFEST.name}")
    for arch, artifact in updated.new.artifacts.items():
        print(f"  {arch}: {artifact.url} sha256 {artifact.sha256}")
    for filename, extra in updated.new.extras.items():
        print(f"  {filename}: {extra.url} sha256 {extra.sha256}")
    print(f"next: ./quality tools install {args.name}, then review, stage, and commit")
    return 0
