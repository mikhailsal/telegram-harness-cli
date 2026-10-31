import json

import pytest

from quality.boundedio import JsonValue, ReportError
from quality.testing import CaseResult, Outcome, parse_playwright, read_playwright

PROJECTS: list[JsonValue] = ["narrow", "wide"]


def entry(
    name: str = "shows the page",
    project: str = "narrow",
    outcome: str = "expected",
    expected: str = "passed",
    **fields: JsonValue,
) -> dict[str, JsonValue]:
    found: dict[str, JsonValue] = {
        "file": "e2e/page.spec.ts",
        "title": ["page", name],
        "project": project,
        "outcome": outcome,
        "expected": expected,
        "attachments": [],
    }
    return {**found, **fields}


def report(*tests: dict[str, JsonValue], **fields: JsonValue) -> bytes:
    document: dict[str, JsonValue] = {"projects": PROJECTS, "tests": list(tests), "errors": []}
    return json.dumps({**document, **fields}).encode()


def both(**fields: JsonValue) -> tuple[dict[str, JsonValue], dict[str, JsonValue]]:
    return {**entry(), **fields}, entry(project="wide")


def test_a_test_is_one_case_named_by_its_file_and_titles() -> None:
    attached: list[JsonValue] = [
        {"name": "trace", "path": "/run/trace.zip"},
        {"name": "accessibility-scan", "path": "/run/1.bin"},
    ]
    parsed = read_playwright(report(*both(attachments=attached)), "results")
    assert parsed.projects == ("narrow", "wide")
    first = parsed.tests[0]
    assert first.name == "e2e/page.spec.ts::page > shows the page"
    assert first.attached("trace") == ("/run/trace.zip",)
    assert first.attached("missing") == ()
    assert parsed.results().cases == (CaseResult(first.name, Outcome.PASSED),)
    assert not parsed.results().focused


@pytest.mark.parametrize(
    ("fields", "outcome"),
    [
        ({"outcome": "unexpected"}, Outcome.FAILED),
        ({"outcome": "flaky"}, Outcome.FAILED),
        ({"outcome": "skipped"}, Outcome.SKIPPED),
        ({"expected": "failed"}, Outcome.EXPECTED_FAILURE),
    ],
)
def test_the_worst_outcome_of_any_viewport_is_the_case_outcome(
    fields: dict[str, JsonValue], outcome: Outcome
) -> None:
    (case,) = parse_playwright(report(*both(**fields)), "results").cases
    assert case.outcome is outcome


def test_a_test_that_did_not_run_in_a_viewport_is_skipped() -> None:
    (case,) = parse_playwright(report(entry()), "results").cases
    assert case.outcome is Outcome.SKIPPED


def test_a_failure_outweighs_a_skip_and_a_skip_an_expected_failure() -> None:
    failed = report(entry(outcome="skipped"), entry(project="wide", outcome="unexpected"))
    assert parse_playwright(failed, "results").cases[0].outcome is Outcome.FAILED
    skipped = report(entry(expected="failed"), entry(project="wide", outcome="skipped"))
    assert parse_playwright(skipped, "results").cases[0].outcome is Outcome.SKIPPED


def test_a_run_refused_for_a_focused_test_is_focused() -> None:
    errors: list[JsonValue] = [
        "Error: item focused with '.only' is not allowed due to the 'forbidOnly' option"
    ]
    parsed = read_playwright(report(errors=errors), "results")
    assert parsed.errors == tuple(errors)
    assert parsed.results().focused
    assert parsed.results().cases == ()


@pytest.mark.parametrize(
    ("data", "problem"),
    [
        (b"[]", "the results must be a JSON object"),
        (report(projects=[]), "the run declared no projects"),
        (report(projects="narrow"), "'projects' must be a list"),
        (report(projects=[1]), "'projects' must be a list of strings"),
        (report(tests=[1]), "every item of 'tests' must be an object"),
        (report(entry(project="other")), "a test ran in the undeclared project 'other'"),
        (report(entry(title=None)), "'title' must be a list"),
        (report(entry(title=[])), "a test has an empty title"),
        (report(entry(outcome="lost")), "unknown outcome 'lost'"),
        (report(entry(file="")), "'file' must be a non-empty string"),
        (report(entry(attachments=[{"name": "trace"}])), "'path' must be a non-empty string"),
        (report(errors=None), "'errors' must be a list"),
        (report(entry(), entry()), "reported twice for 'narrow'"),
    ],
)
def test_incomplete_results_are_report_errors(data: bytes, problem: str) -> None:
    with pytest.raises(ReportError, match=problem):
        read_playwright(data, "results")


def test_an_entry_without_a_title_list_cannot_be_read() -> None:
    broken = entry()
    del broken["title"]
    with pytest.raises(ReportError, match="'title' must be a list"):
        read_playwright(report(broken), "results")
