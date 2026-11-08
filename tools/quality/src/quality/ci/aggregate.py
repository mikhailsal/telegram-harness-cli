"""The workflow named by the `[ci]` policy: its aggregate job and its triggers (R09)."""

from __future__ import annotations

from collections.abc import Iterator

from quality.ci.lanes import check_lanes
from quality.ci.workflow import Table, events, finding, items, jobs, mapping, steps
from quality.diagnostics import Diagnostic, Location, Rule
from quality.policy import CiPolicy

AGGREGATE = Rule(
    id="ci.aggregate",
    title="The required workflow does not aggregate its jobs safely",
    requirement="R09",
    explanation=(
        "The workflow in `[ci].workflow` runs on `pull_request` and `merge_group`. Its "
        "aggregate job, the one required status, runs `if: always()`, needs exactly the "
        "`required_jobs`, and passes `toJSON(needs)` to `quality ci verdict`, which fails "
        "unless every required job succeeded. Required jobs have no `if`, so none is "
        "skipped by a condition."
    ),
    remedy="Align the workflow with the `[ci]` table in quality.toml.",
)
PATH_FILTER = Rule(
    id="ci.path-filter",
    title="The required workflow is filtered by paths",
    requirement="R09",
    explanation=(
        "The gate decides which components a change affects from the whole candidate. "
        "`paths` and `paths-ignore` would let a change that touches only filtered paths "
        "skip the required workflow."
    ),
    remedy="Remove `paths` and `paths-ignore` from the triggers of the required workflow.",
)
RULES = (AGGREGATE, PATH_FILTER)

REQUIRED_EVENTS = ("pull_request", "merge_group")
FILTERS = ("paths", "paths-ignore")
ALWAYS = frozenset({"always()", "${{ always() }}"})
VERDICT = "quality ci verdict"
NEEDS = "toJSON(needs)"


def _needs(job: Table) -> list[str]:
    value = job.get("needs")
    if isinstance(value, str):
        return [value]
    return [item for item in items(value) if isinstance(item, str)]


def _texts(job: Table) -> Iterator[str]:
    for step in steps(job):
        yield from (value for value in step.values() if isinstance(value, str))
        yield from (str(value) for value in (mapping(step.get("env")) or {}).values())
    yield from (str(value) for value in (mapping(job.get("env")) or {}).values())


def _aggregate(path: str, name: str, job: Table, required: list[str]) -> Iterator[Diagnostic]:
    condition = job.get("if")
    if not isinstance(condition, str) or condition.strip() not in ALWAYS:
        yield finding(AGGREGATE, path, f"job '{name}' does not run 'if: always()'", name)
    needs = _needs(job)
    if sorted(needs) != sorted(required):
        message = f"job '{name}' needs {sorted(needs)}, the policy requires {sorted(required)}"
        yield finding(AGGREGATE, path, message, name)
    texts = list(_texts(job))
    runs = [step.get("run") for step in steps(job)]
    if not any(isinstance(run, str) and VERDICT in run for run in runs):
        yield finding(AGGREGATE, path, f"job '{name}' does not run '{VERDICT}'", name)
    if not any(NEEDS in text for text in texts):
        yield finding(AGGREGATE, path, f"job '{name}' does not pass '{NEEDS}'", name)


def _triggers(path: str, document: Table) -> Iterator[Diagnostic]:
    triggers = events(document)
    for event in REQUIRED_EVENTS:
        if event not in triggers:
            message = f"the required workflow does not run on '{event}'"
            yield finding(AGGREGATE, path, message, event)
    for event, settings in triggers.items():
        for key in FILTERS:
            if key in (mapping(settings) or {}):
                message = f"the trigger '{event}' is filtered by '{key}'"
                yield finding(PATH_FILTER, path, message, f"{event}:{key}")


def check_required(document: Table | None, policy: CiPolicy) -> Iterator[Diagnostic]:
    """Check the required workflow; None means the candidate has no readable workflow there."""
    path = policy.workflow
    if document is None:
        message = f"the required workflow '{path}' is missing or unreadable"
        yield AGGREGATE.diagnostic(message, location=Location(path), data={"symbol": path})
        return
    yield from _triggers(path, document)
    found = jobs(document)
    aggregate = found.get(policy.aggregate_job)
    if aggregate is None:
        message = f"the aggregate job '{policy.aggregate_job}' does not exist"
        yield finding(AGGREGATE, path, message, policy.aggregate_job)
    else:
        yield from _aggregate(path, policy.aggregate_job, aggregate, policy.required_jobs)
    for name in policy.required_jobs:
        job = found.get(name)
        if job is None:
            yield finding(AGGREGATE, path, f"the required job '{name}' does not exist", name)
        elif "if" in job:
            message = f"the required job '{name}' has a condition and may be skipped"
            yield finding(AGGREGATE, path, message, name)
    yield from check_lanes(path, (found[name] for name in policy.required_jobs if name in found))
