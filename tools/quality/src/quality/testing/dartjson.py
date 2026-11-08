"""Test results from the JSON reporter of `package:test` (`dart test --file-reporter json:...`).

The report is one JSON event per line. Suites, test starts and completions, and
errors are read; every other event (`start`, `allSuites`, `group`, `print`,
`debug`) is ignored. A case is named `<suite path>::<test name>`, where the
suite path is relative to the directory `dart test` ran in.

The runner reports its own pseudo-tests, such as `loading <file>` and
`(setUpAll)`, as hidden. A hidden case is dropped only when it passed, so a
suite that fails to load still counts as an error. An `error` event for a test
that already passed makes it an error, because `package:test` reports errors
raised after a test completed that way. A test that started but never
completed is an error too. A report without the final `done` event, a line that
is not a JSON object, a completion for an unknown or already completed test, or
an event with missing fields is a ReportError: the run's evidence is invalid.

When a test is marked `solo`, `package:test` skips every other test with the
reason `does not have "solo"`; such a run is focused.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import NoReturn

from quality.boundedio import JsonValue, ReportError, parse_json
from quality.testing.model import CaseResult, Outcome, SuiteResults

type Event = dict[str, JsonValue]

RESULTS = {"success": Outcome.PASSED, "failure": Outcome.FAILED, "error": Outcome.ERROR}
PASSING = frozenset({Outcome.PASSED, Outcome.SKIPPED})
UNFOCUSED = 'does not have "solo"'


@dataclass(slots=True)
class _Case:
    name: str
    outcome: Outcome | None = None
    hidden: bool = False
    errored: bool = False

    def result(self) -> CaseResult | None:
        outcome = self.outcome or Outcome.ERROR
        if self.errored and outcome in PASSING:
            outcome = Outcome.ERROR
        if self.hidden and outcome in PASSING:
            return None
        return CaseResult(self.name, outcome)


class _Stream:
    """The state of one report while its events are read in order."""

    def __init__(self, origin: str) -> None:
        self.origin = origin
        self.suites: dict[int, str | None] = {}
        self.cases: dict[int, _Case] = {}
        self.done = False
        self.focused = False
        self.handlers: dict[str, Callable[[Event], None]] = {
            "suite": self._suite,
            "testStart": self._start,
            "testDone": self._done,
            "error": self._error,
            "done": self._finish,
        }

    def fail(self, problem: str) -> NoReturn:
        msg = f"{self.origin}: {problem}"
        raise ReportError(msg)

    def read(self, event: JsonValue) -> None:
        if not isinstance(event, dict):
            self.fail("an event is not a JSON object")
        kind = event.get("type")
        handler = self.handlers.get(kind) if isinstance(kind, str) else None
        if handler is not None:
            handler(event)

    def _field(self, event: Event, key: str) -> Event:
        value = event.get(key)
        if not isinstance(value, dict):
            self.fail(f"an event has no '{key}' object")
        return value

    def _integer(self, event: Event, key: str) -> int:
        value = event.get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            self.fail(f"an event has no integer '{key}'")
        return value

    def _case(self, event: Event) -> _Case:
        identifier = self._integer(event, "testID")
        case = self.cases.get(identifier)
        if case is None:
            self.fail(f"an event for an unknown test {identifier}")
        return case

    def _suite(self, event: Event) -> None:
        suite = self._field(event, "suite")
        path = suite.get("path")
        self.suites[self._integer(suite, "id")] = path if isinstance(path, str) else None

    def _start(self, event: Event) -> None:
        test = self._field(event, "test")
        identifier = self._integer(test, "id")
        name = test.get("name")
        suite = self._integer(test, "suiteID")
        if identifier in self.cases or suite not in self.suites or not isinstance(name, str):
            self.fail(f"an invalid or repeated start of test {identifier}")
        path = self.suites[suite]
        self.cases[identifier] = _Case(f"{path}::{name}" if path else name)
        metadata = test.get("metadata")
        if isinstance(metadata, dict) and metadata.get("skipReason") == UNFOCUSED:
            self.focused = True

    def _done(self, event: Event) -> None:
        case = self._case(event)
        result = RESULTS.get(str(event.get("result")))
        if case.outcome is not None or result is None:
            self.fail(f"an invalid or repeated completion of '{case.name}'")
        skipped = event.get("skipped") is True and result is Outcome.PASSED
        case.outcome = Outcome.SKIPPED if skipped else result
        case.hidden = event.get("hidden") is True

    def _error(self, event: Event) -> None:
        self._case(event).errored = True

    def _finish(self, _event: Event) -> None:
        self.done = True


def parse_dart_json(data: bytes, origin: str) -> SuiteResults:
    """Parse a JSON reporter event stream into the cases it ran."""
    stream = _Stream(origin)
    for line in data.splitlines():
        if line.strip():
            stream.read(parse_json(line, origin))
    if not stream.done:
        stream.fail("the report has no 'done' event; the run did not finish")
    results = (case.result() for case in stream.cases.values())
    return SuiteResults(tuple(result for result in results if result is not None), stream.focused)
