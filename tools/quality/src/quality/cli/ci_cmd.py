"""`quality ci verdict`: the aggregate CI job's decision over its required jobs (R09)."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from quality.boundedio import ReportError, read_bounded
from quality.ci import ResultsError, job_verdict, parse_results
from quality.ci.verdict import RESULTS_LIMIT
from quality.cli import Subcommands
from quality.diagnostics import Diagnostic, Outcome
from quality.diagnostics.render import outcome_to_json, render_text
from quality.gitio import GitioError, Repository, read_path
from quality.policy import CiPolicy, parse_policy
from quality.policy.load import MAX_POLICY_BYTES, POLICY_FILE

NAME = "ci"
ACTIONS = frozenset({"verdict"})


def register(commands: Subcommands) -> None:
    """Add the `ci` subcommand group."""
    parser = commands.add_parser(
        NAME,
        help="decide the required CI status from the results of the jobs it needs",
        description="Commands that CI wrappers call after the gate jobs have run.",
    )
    actions = parser.add_subparsers(dest="action", metavar="<action>", required=True)
    verdict = actions.add_parser(
        "verdict",
        help="fail unless every required CI job succeeded",
        description="Read job results in the form of GitHub's toJSON(needs) and the [ci] "
        "table of the revision's quality.toml. Exit 0 when every required job succeeded, "
        "1 when one failed, was cancelled or skipped, is missing, or is not expected, and "
        "2 when the results or the policy are unusable.",
    )
    verdict.add_argument("--needs", type=Path, required=True, help="file with the job results")
    verdict.add_argument(
        "--rev", default="HEAD", help="revision whose policy names the required jobs"
    )
    verdict.add_argument("--json", action="store_true", help="print a JSON report")
    verdict.set_defaults(handler=run_verdict)


def ci_policy(repo: Repository, revision: str) -> CiPolicy | list[Diagnostic]:
    """Return the `[ci]` table of a revision's policy, or why there is none."""
    tree = repo.tree_of(repo.resolve_commit(revision))
    data = read_path(repo, tree, POLICY_FILE, MAX_POLICY_BYTES)
    if data is None:
        msg = f"'{revision}' has no {POLICY_FILE} of at most {MAX_POLICY_BYTES} bytes"
        raise ResultsError(msg)
    loaded = parse_policy(data)
    if loaded.policy is None:
        return list(loaded.diagnostics)
    if loaded.policy.ci is None:
        msg = f"the {POLICY_FILE} of '{revision}' has no [ci] table"
        raise ResultsError(msg)
    return loaded.policy.ci


def verdict(args: argparse.Namespace) -> list[Diagnostic]:
    """Return the findings of the verdict; none means every required job succeeded."""
    try:
        repo = Repository.discover(Path.cwd(), os.environ)
        policy = ci_policy(repo, args.rev)
        if isinstance(policy, list):
            return policy
        results = parse_results(read_bounded(args.needs, RESULTS_LIMIT))
    except GitioError as error:
        return [error.diagnostic]
    except ReportError as error:
        return [ResultsError(str(error)).diagnostic]
    except ResultsError as error:
        return [error.diagnostic]
    return list(job_verdict(results, policy.required_jobs))


def run_verdict(args: argparse.Namespace) -> int:
    """Print the verdict over the required jobs."""
    outcome = Outcome.of(verdict(args))
    command = f"{NAME} verdict"
    sys.stdout.write(
        outcome_to_json(outcome, command=command) if args.json else render_text(outcome)
    )
    return int(outcome.exit_code())
