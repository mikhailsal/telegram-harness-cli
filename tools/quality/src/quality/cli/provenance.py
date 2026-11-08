"""Verdict commands run with the candidate's own core (decision 0004).

`check`, `gate`, `test`, and `self-test` set `judged`; before their handler runs,
`judge` decides whether this core may judge the candidate. It hands the command
to the candidate's core when the cores differ, and it never skips the
comparison: there is no option or variable to turn it off.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

import quality
from quality.cli.verdict import candidate_of
from quality.diagnostics import Diagnostic, Outcome
from quality.diagnostics.render import outcome_to_json, render_text
from quality.engine.gate import GateError
from quality.engine.provenance import (
    LOCK,
    Provenance,
    candidate_core,
    in_snapshot,
    lock_findings,
    provenance,
    run_candidate_core,
)
from quality.gitio import GitioError
from quality.gitio.errors import GIT_CANDIDATE_CHANGED

# The directory of the running core: `tools/quality` in a checkout.
RUNNING_CORE = Path(quality.__file__).resolve().parents[2]


def _verdict(args: argparse.Namespace, diagnostics: Sequence[Diagnostic]) -> int:
    outcome = Outcome.of(tuple(diagnostics))
    command: str = args.command
    text = outcome_to_json(outcome, command=command) if args.json else render_text(outcome)
    sys.stdout.write(text)
    return int(outcome.exit_code())


def judge(args: argparse.Namespace, argv: Sequence[str], root: Path = RUNNING_CORE) -> int | None:
    """Return the verdict of the candidate's core, or None when this core is the candidate's.

    A candidate that cannot be resolved returns None too: the handler then
    reports the same Git or usage error itself.
    """
    try:
        repo, candidate = candidate_of(args)
        core = candidate_core(repo, candidate.tree)
    except GitioError, GateError:
        return None
    if core is None:
        return None
    problems = lock_findings(core, root / LOCK)
    if problems:
        return _verdict(args, problems)
    if provenance(core, root) is Provenance.CURRENT:
        return None
    if in_snapshot(root):
        message = f"{candidate.description} changed while its own core was being prepared"
        return _verdict(args, [GIT_CANDIDATE_CHANGED.diagnostic(message)])
    return run_candidate_core(repo, core, argv, os.environ)
