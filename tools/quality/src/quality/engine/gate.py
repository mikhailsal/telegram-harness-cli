"""`quality gate`: the candidate of a commit, push, pull-request, or merge-queue run.

The gate needs no CI variables: a wrapper translates its provider's event
into explicit revisions (decision 0015). The commit stage checks an index, or
a pushed range with `--base` and `--head`; the merge stage checks the tested
tree of a pull request (`--tested`, the head by default) or of a merge-queue
entry (`--queue`). Both stages check every active component, and a range
also checks every blob of its new commits. A lane checks one part of that,
as a CI job does (decision 0018).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from quality.ci import Lane
from quality.diagnostics import Diagnostic, Kind, Rule, Severity
from quality.gitio import Candidate, Repository, merge_candidate, push_candidate, staged
from quality.policy.scenarios import Stage

ARGUMENTS = Rule(
    id="gate.arguments",
    title="The gate's revisions do not describe one candidate",
    requirement="R09",
    explanation=(
        "The merge stage needs the base and head revisions; the commit stage takes an index "
        "or a pushed range with both of them. A tested revision or merge-queue mode belongs "
        "to the merge stage, and the reports directory must be new or empty."
    ),
    remedy="Pass the revisions your stage needs; `quality gate --help` lists them.",
    kind=Kind.USAGE,
)
LANE = Rule(
    id="gate.lane",
    title="The gate checked one lane",
    requirement="R09",
    explanation=(
        "CI runs the lanes of the gate in separate required jobs: `main` runs the common "
        "gates and the history checks and checks every component that does not run in a "
        "browser, and `browser` checks the browser components. One lane is not the complete "
        "gate; the aggregate job requires both."
    ),
    remedy="Nothing to do; without --lane the gate checks everything.",
    severity=Severity.INFO,
)
RULES = (ARGUMENTS, LANE)


class GateError(Exception):
    """The gate's arguments do not describe one candidate."""

    def __init__(self, diagnostic: Diagnostic) -> None:
        """Wrap a usage diagnostic."""
        super().__init__(diagnostic.message)
        self.diagnostic = diagnostic


@dataclass(frozen=True, slots=True)
class Revisions:
    """What a wrapper passes to the gate; `index` is an alternate index of the commit stage."""

    stage: Stage
    base: str | None = None
    head: str | None = None
    tested: str | None = None
    queue: bool = False
    index: Path | None = None


def _merge_problem(revisions: Revisions, *, ranged: bool) -> str | None:
    if not ranged:
        return "the merge stage needs --base and --head"
    if revisions.index is not None:
        return "--index belongs to the commit stage"
    if revisions.queue and revisions.tested is not None:
        return "a merge-queue entry is tested at its head; omit --tested"
    return None


def _commit_problem(revisions: Revisions, *, ranged: bool) -> str | None:
    if revisions.tested is not None or revisions.queue:
        return "--tested and --queue belong to the merge stage"
    if ranged and revisions.index is not None:
        return "--index and a pushed range exclude each other"
    return None


def _problem(revisions: Revisions) -> str | None:
    given = (revisions.base is not None, revisions.head is not None)
    if any(given) and not all(given):
        return "--base and --head go together"
    if revisions.stage is Stage.MERGE:
        return _merge_problem(revisions, ranged=all(given))
    return _commit_problem(revisions, ranged=all(given))


def usage_error(message: str) -> GateError:
    """Return the error for gate arguments that do not describe one candidate."""
    return GateError(ARGUMENTS.diagnostic(message))


def gate_candidate(repo: Repository, revisions: Revisions) -> Candidate:
    """Translate explicit revisions into the candidate the gate checks."""
    problem = _problem(revisions)
    if problem is not None:
        raise usage_error(problem)
    base, head = revisions.base, revisions.head
    if base is None or head is None:
        return staged(repo, revisions.index)
    if revisions.stage is Stage.COMMIT:
        return push_candidate(repo, base, head)
    return merge_candidate(repo, base, head, revisions.tested, queue=revisions.queue)


def lane_note(lane: Lane | None, active: Iterable[str]) -> list[Diagnostic]:
    """Say what the other lane checks, given the active browser components' IDs."""
    if lane is None:
        return []
    browsers = ", ".join(active)
    if lane is Lane.MAIN:
        message = f"lane main: the browser lane checks {browsers or 'no component'}"
    elif browsers:
        message = f"lane browser: checks {browsers}; the main lane checks everything else"
    else:
        message = "lane browser: no active component runs in a browser, so nothing to check"
    return [LANE.diagnostic(message)]
