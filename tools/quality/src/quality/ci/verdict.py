"""The verdict of the aggregate CI job over the results of the jobs it needs (R09).

The provider passes the results as JSON, GitHub's `toJSON(needs)`: an object
from job ID to an object with a `result`. Only `success` counts; failed,
cancelled, and skipped jobs, jobs that are missing, and jobs the policy does
not expect all fail the verdict.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence

from quality.boundedio import JsonValue, ReportError, parse_json
from quality.diagnostics import Diagnostic, Kind, Rule

FAILED = Rule(
    id="ci.job-failed",
    title="A required CI job did not succeed",
    requirement="R09",
    explanation=(
        "Every job in `[ci].required_jobs` must finish with the result `success`. A failed, "
        "cancelled, or skipped job, or any other result, fails the aggregate job."
    ),
    remedy="Open the job's log, fix the cause, and run the workflow again.",
)
MISSING = Rule(
    id="ci.job-missing",
    title="A required CI job has no result",
    requirement="R09",
    explanation=(
        "A job in `[ci].required_jobs` is absent from the results the aggregate job "
        "received, so it was not needed by the aggregate job or not run at all."
    ),
    remedy="Add the job to the aggregate job's `needs`, or correct `[ci].required_jobs`.",
)
UNEXPECTED = Rule(
    id="ci.job-unexpected",
    title="The aggregate job received a result the policy does not expect",
    requirement="R09",
    explanation=(
        "The workflow and the policy of the same revision disagree on the required jobs, "
        "so the verdict cannot tell which results matter."
    ),
    remedy="Make the aggregate job's `needs` equal `[ci].required_jobs`.",
)
RESULTS = Rule(
    id="ci.results",
    title="Job results are unreadable",
    requirement="R09",
    explanation=(
        "The results file must hold a JSON object from job ID to an object with a string "
        "`result`; the policy of the revision must have a `[ci]` table."
    ),
    remedy="Pass `${{ toJSON(needs) }}` through a file, and declare `[ci]` in quality.toml.",
    kind=Kind.USAGE,
)
RULES = (FAILED, MISSING, UNEXPECTED, RESULTS)

SUCCESS = "success"
RESULTS_LIMIT = 4 * 1024 * 1024


class ResultsError(Exception):
    """The job results cannot be used."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.diagnostic = RESULTS.diagnostic(message)


def _result(job: str, value: JsonValue) -> str:
    result = value.get("result") if isinstance(value, dict) else None
    if not isinstance(result, str):
        msg = f"job '{job}' has no string 'result'"
        raise ResultsError(msg)
    return result


def parse_results(data: bytes) -> dict[str, str]:
    """Return each job's result from `toJSON(needs)` output."""
    if len(data) > RESULTS_LIMIT:
        msg = f"the results exceed {RESULTS_LIMIT} bytes"
        raise ResultsError(msg)
    try:
        document = parse_json(data, "job results")
    except ReportError as error:
        raise ResultsError(str(error)) from error
    if not isinstance(document, dict):
        msg = "the results are not a JSON object"
        raise ResultsError(msg)
    return {job: _result(job, value) for job, value in document.items()}


def job_verdict(results: Mapping[str, str], required: Sequence[str]) -> Iterator[Diagnostic]:
    """Report every required job that did not succeed and every unexpected result."""
    for job in required:
        result = results.get(job)
        if result is None:
            yield MISSING.diagnostic(f"required job '{job}' has no result", data={"symbol": job})
        elif result != SUCCESS:
            message = f"required job '{job}' finished with '{result}'"
            yield FAILED.diagnostic(message, data={"symbol": job})
    for job in sorted(set(results) - set(required)):
        message = f"job '{job}' is needed by the aggregate job but not required by the policy"
        yield UNEXPECTED.diagnostic(message, data={"symbol": job})
