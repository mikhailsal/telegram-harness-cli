import json
from pathlib import Path

import pytest

from quality.boundedio import JsonValue, ReportError
from quality.testing import Outcome, SuiteResults, parse_dart_json

SAMPLES = Path(__file__).parent / "samples"
PASSED, FAILED, ERROR, SKIPPED = Outcome.PASSED, Outcome.FAILED, Outcome.ERROR, Outcome.SKIPPED
SUITE: dict[str, JsonValue] = {"type": "suite", "suite": {"id": 0, "path": "test/a_test.dart"}}
DONE: dict[str, JsonValue] = {"type": "done", "success": True}


def start(identifier: int, name: str = "case", suite: JsonValue = 0) -> dict[str, JsonValue]:
    return {"type": "testStart", "test": {"id": identifier, "name": name, "suiteID": suite}}


def done(identifier: JsonValue, result: str = "success", **flags: bool) -> dict[str, JsonValue]:
    return {"type": "testDone", "testID": identifier, "result": result, **flags}


def stream(*events: JsonValue) -> bytes:
    return b"\n".join(json.dumps(event).encode() for event in events) + b"\n"


def outcomes(results: SuiteResults) -> list[tuple[str, Outcome]]:
    return [(case.name, case.outcome) for case in results.cases]


def test_a_real_report_is_parsed() -> None:
    results = parse_dart_json((SAMPLES / "dart.json").read_bytes(), "dart.json")
    suite = "test/sample_test.dart"
    assert outcomes(results) == [
        (f"{suite}::passes", PASSED),
        (f"{suite}::fails", FAILED),
        (f"{suite}::errors", ERROR),
        (f"{suite}::skipped", SKIPPED),
        (f"{suite}::group nested", PASSED),
        # A timer threw after the test had passed; package:test reports it afterwards.
        (f"{suite}::errors after passing", ERROR),
        (f"{suite}::waits", PASSED),
    ]
    assert (results.executed, results.count(FAILED, ERROR)) == (6, 3)


def test_a_solo_test_makes_the_run_focused() -> None:
    unfocused = start(2, "other")
    unfocused["test"] = {
        "id": 2,
        "name": "other",
        "suiteID": 0,
        "metadata": {"skip": True, "skipReason": 'does not have "solo"'},
    }
    data = stream(SUITE, start(1, "solo"), done(1), unfocused, done(2, skipped=True), DONE)
    results = parse_dart_json(data, "r")
    assert results.focused
    assert outcomes(results) == [
        ("test/a_test.dart::solo", PASSED),
        ("test/a_test.dart::other", SKIPPED),
    ]
    skipped = start(3, "plain")
    skipped["test"] = {"id": 3, "name": "plain", "suiteID": 0, "metadata": {"skipReason": "later"}}
    assert not parse_dart_json(stream(SUITE, skipped, done(3, skipped=True), DONE), "r").focused


def test_hidden_cases_count_only_when_they_fail() -> None:
    data = stream(
        SUITE,
        start(1, "loading test/a_test.dart"),
        done(1, hidden=True),
        start(2, "(setUpAll)"),
        done(2, "failure", hidden=True),
        start(3, "(tearDownAll)"),
        done(3, skipped=True, hidden=True),
        DONE,
    )
    assert outcomes(parse_dart_json(data, "r")) == [("test/a_test.dart::(setUpAll)", FAILED)]


def test_a_test_that_never_completed_is_an_error_and_unknown_events_are_ignored() -> None:
    data = stream(
        {"type": "start", "pid": 1},
        {"type": "suite", "suite": {"id": 0, "path": None}},
        start(1, "hangs"),
        {"type": "print", "testID": 1, "message": "x"},
        {"kind": "untyped"},
        DONE,
    )
    assert outcomes(parse_dart_json(b"\n" + data, "r")) == [("hangs", ERROR)]


def test_a_report_without_tests_has_no_cases() -> None:
    data = stream({"type": "allSuites", "count": 0}, DONE)
    assert parse_dart_json(data, "r").cases == ()


@pytest.mark.parametrize(
    ("events", "message"),
    [
        (("not an object",), "an event is not a JSON object"),
        (({"type": "suite"},), "no 'suite' object"),
        (({"type": "suite", "suite": {"id": True}},), "no integer 'id'"),
        ((SUITE, start(1), start(1)), "repeated start of test 1"),
        ((SUITE, start(1, suite=7)), "invalid or repeated start of test 1"),
        ((SUITE, {"type": "testStart", "test": {"id": 1, "suiteID": 0}}), "start of test 1"),
        ((SUITE, done(4)), "an event for an unknown test 4"),
        ((SUITE, done("4")), "no integer 'testID'"),
        ((SUITE, {"type": "error", "testID": 9}), "unknown test 9"),
        ((SUITE, start(1), done(1), done(1)), "repeated completion of 'test/a_test.dart::case'"),
        ((SUITE, start(1), done(1, "timeout")), "invalid or repeated completion"),
    ],
)
def test_malformed_reports_are_rejected(events: tuple[JsonValue, ...], message: str) -> None:
    with pytest.raises(ReportError, match=r"^results\.json: ") as raised:
        parse_dart_json(stream(*events, DONE), "results.json")
    assert message in str(raised.value)


def test_a_report_of_a_run_that_did_not_finish_is_rejected() -> None:
    with pytest.raises(ReportError, match="no 'done' event; the run did not finish"):
        parse_dart_json(stream(SUITE, start(1), done(1)), "results.json")


def test_a_line_that_is_not_json_is_rejected() -> None:
    with pytest.raises(ReportError, match=r"malformed JSON in results\.json"):
        parse_dart_json(b'{"type": "done"}\n{not json\n', "results.json")
