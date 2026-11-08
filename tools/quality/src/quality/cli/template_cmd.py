"""`quality template export`: write a commit as a project template (decision 0016)."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from quality.catalog import build_registry
from quality.cli import Subcommands
from quality.diagnostics import Diagnostic, Outcome
from quality.diagnostics.render import render_text
from quality.gitio import GitioError, Repository
from quality.template import ExportError, export_tree

NAME = "template"
ACTIONS = frozenset({"export"})


def register(commands: Subcommands) -> None:
    """Add the `template` subcommand group."""
    parser = commands.add_parser(
        NAME,
        help="export this repository as a project template",
        description="Write a commit of this repository as the template a new project starts "
        "from, without the maintainer content its policy names in [template].",
    )
    actions = parser.add_subparsers(dest="action", metavar="<action>", required=True)
    export = actions.add_parser(
        "export",
        help="write the project template to a new directory",
        description="Write the tree of a commit (HEAD by default) to a new or empty directory, "
        "without the paths in [template].exclude and without the maintainer-only regions of "
        "the files in [template].fenced, and make it a repository whose root commit, made with "
        "your Git identity, holds those files and names the template commit. Untracked and "
        "unstaged files are never exported, and the exported policy is validated before the "
        "directory appears. Exit 0 when exported, 2 when the template or the target is "
        "invalid, 3 on Git errors, such as a missing identity.",
    )
    export.add_argument("directory", type=Path, help="the new or empty target directory")
    export.add_argument("--rev", default="HEAD", help="the commit to export (default: HEAD)")
    export.set_defaults(handler=run_export)


def run_export(args: argparse.Namespace) -> int:
    """Export a commit and print what was written and what comes next."""
    found: list[Diagnostic] = []
    try:
        repo = Repository.discover(Path.cwd(), os.environ)
        commit = repo.resolve_commit(args.rev)
        target = Path(args.directory)
        exported = export_tree(repo, commit, target, build_registry().ids(), os.environ)
    except GitioError as error:
        found.append(error.diagnostic)
    except ExportError as error:
        found.extend(error.diagnostics)
    else:
        print(
            f"exported {exported.files} files of {args.rev} to {exported.target}; "
            f"left out {exported.left_out}, rewrote {len(exported.fenced)}"
        )
        print(f"committed them as {exported.commit} on {exported.branch}")
        print(f"next: cd {args.directory}, ./quality bootstrap, then add your components")
    outcome = Outcome.of(found)
    if found:
        sys.stdout.write(render_text(outcome))
    return int(outcome.exit_code())
