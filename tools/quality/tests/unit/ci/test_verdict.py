import json

import pytest

from quality.ci import ResultsError, job_verdict, parse_results
from quality.ci.verdict import RESULTS_LIMIT

REQUIRED = ["gate", "audit"]


def needs(**results: str) -> bytes:
    outputs: dict[str, str] = {}
    document = {job: {"result": result, "outputs": outputs} for job, result in results.items()}
    return json.dumps(document).encode()


def verdict(data: bytes) -> list[tuple[str, str]]:
    return [(d.rule, d.message) for d in job_verdict(parse_results(data), REQUIRED)]


def test_every_required_job_succeeding_passes() -> None:
    assert verdict(needs(gate="success", audit="success")) == []


@pytest.mark.parametrize("result", ["failure", "cancelled", "skipped", "neutral"])
def test_any_other_result_fails(result: str) -> None:
    assert verdict(needs(gate="success", audit=result)) == [
        ("ci.job-failed", f"required job 'audit' finished with '{result}'")
    ]


def test_a_missing_required_job_fails() -> None:
    assert verdict(needs(gate="success")) == [
        ("ci.job-missing", "required job 'audit' has no result")
    ]


def test_an_empty_needs_context_fails_every_required_job() -> None:
    assert [rule for rule, _ in verdict(b"{}")] == ["ci.job-missing", "ci.job-missing"]


def test_an_unexpected_job_fails_even_when_it_succeeded() -> None:
    assert verdict(needs(gate="success", audit="success", lint="success")) == [
        (
            "ci.job-unexpected",
            "job 'lint' is needed by the aggregate job but not required by the policy",
        )
    ]


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"[]", "the results are not a JSON object"),
        (b'{"gate": "success"}', "job 'gate' has no string 'result'"),
        (b'{"gate": {"result": 1}}', "job 'gate' has no string 'result'"),
        (b'{"gate": {}}', "job 'gate' has no string 'result'"),
    ],
)
def test_malformed_results_are_usage_errors(data: bytes, message: str) -> None:
    with pytest.raises(ResultsError) as raised:
        parse_results(data)
    assert raised.value.diagnostic.rule == "ci.results"
    assert raised.value.diagnostic.message == message
    assert raised.value.diagnostic.kind == "usage"


def test_unparsable_results_are_usage_errors() -> None:
    with pytest.raises(ResultsError) as raised:
        parse_results(b"{")
    assert "job results" in raised.value.diagnostic.message


def test_oversized_results_are_refused_before_parsing() -> None:
    with pytest.raises(ResultsError, match="exceed"):
        parse_results(b" " * (RESULTS_LIMIT + 1))


@pytest.mark.parametrize("result", ["failure", "cancelled", "skipped"])
def test_a_browser_job_that_did_not_succeed_fails_the_aggregate(result: str) -> None:
    results = parse_results(needs(gate="success", browser=result, audit="success"))
    assert [(d.rule, d.message) for d in job_verdict(results, ["gate", "browser", "audit"])] == [
        ("ci.job-failed", f"required job 'browser' finished with '{result}'")
    ]


def test_a_browser_job_left_out_of_the_needs_fails_the_aggregate() -> None:
    results = parse_results(needs(gate="success", audit="success"))
    assert [d.rule for d in job_verdict(results, ["gate", "browser", "audit"])] == [
        "ci.job-missing"
    ]
