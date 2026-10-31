"""`quality inventory`: show how discovery classifies the candidate files."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path

from quality.catalog import build_registry
from quality.cli import Subcommands
from quality.diagnostics import Diagnostic, Outcome
from quality.diagnostics.render import outcome_to_json, printable, render_text
from quality.discovery import FileRecord, build_inventory
from quality.gitio import Candidate, GitioError, Repository, materialize, revision, staged
from quality.policy import cross_validate, load_policy

NAME = "inventory"
ACTIONS: frozenset[str] = frozenset()


def register(commands: Subcommands) -> None:
    """Add the `inventory` subcommand."""
    parser = commands.add_parser(
        NAME,
        help="list candidate files with their category, component, and language",
        description="Materialize the candidate (the index, or --rev) in isolation and print "
        "how each file is classified, followed by discovery findings. Exit 0 when discovery "
        "passes, 1 on violations, 2 on policy errors, 3 on Git errors.",
    )
    parser.add_argument("--rev", help="inspect a commit instead of the index")
    parser.add_argument("--json", action="store_true", help="print a JSON report")
    parser.set_defaults(handler=run_inventory)


def index_from(environ: Mapping[str, str], cwd: Path) -> Path | None:
    """Return the index a hook or caller selected with GIT_INDEX_FILE, if any."""
    value = environ.get("GIT_INDEX_FILE")
    return cwd / value if value else None


def _candidate(repo: Repository, rev: str | None) -> Candidate:
    if rev is not None:
        return revision(repo, rev)
    return staged(repo, index_from(os.environ, Path.cwd()))


def _inspect(candidate: Candidate, repo: Repository) -> tuple[list[Diagnostic], list[FileRecord]]:
    with materialize(repo, candidate.tree) as snapshot:
        loaded = load_policy(snapshot.root)
        if loaded.policy is None:
            return list(loaded.diagnostics), []
        problems = cross_validate(loaded.policy, build_registry().ids())
        if problems:
            return list(problems), []
        inventory = build_inventory(loaded.policy, snapshot.root, snapshot.entries)
    candidate.verify_unchanged()
    return list(inventory.diagnostics), list(inventory.files)


def _render_files(files: list[FileRecord]) -> str:
    rows = [
        f"{r.category:<9} {r.component or '-':<16} {r.language or '-':<10} {printable(r.path)}"
        for r in files
    ]
    return "".join(row + "\n" for row in rows)


def run_inventory(args: argparse.Namespace) -> int:
    """Classify every file of the candidate and report discovery findings."""
    try:
        repo = Repository.discover(Path.cwd(), os.environ)
        candidate = _candidate(repo, args.rev)
        diagnostics, files = _inspect(candidate, repo)
    except GitioError as exc:
        diagnostics, files = [exc.diagnostic], []
    outcome = Outcome.of(diagnostics)
    if args.json:
        extra = {"files": [asdict(record) for record in files]}
        sys.stdout.write(outcome_to_json(outcome, command=NAME, extra=extra))
    else:
        sys.stdout.write(_render_files(files) + render_text(outcome))
    return int(outcome.exit_code())
