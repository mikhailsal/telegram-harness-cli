"""The results of Playwright Test as the gate's reporter writes them (decision 0018).

The reporter writes one JSON object: `projects`, the names of the projects
the run declared, one per viewport; `tests`, one entry per test and project
with the test's file relative to the component, its title path below the
file, Playwright's outcome and expected status, and its attachments as files;
and `errors`, the messages of errors raised outside any test.

A browser scenario must pass in every viewport, so the results hold one case
per test, named `<file>::<titles joined by " > ">` as Vitest names them, with
the worst outcome of its projects. A failure, a pass after a retry, and a
pass of a test marked `test.fail()` fail; a skip, or a project that did not
run the test, skips; a failure that `test.fail()` expects is an expected
failure. A report with missing fields, an unknown project or outcome, or a
test reported twice for one project is a ReportError. Playwright refuses to
run a suite with a test marked `.only` and reports an error that names the
option; such a run is focused.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from quality.boundedio import JsonValue, ReportError, parse_json
from quality.testing.model import CaseResult, Outcome, SuiteResults

type Entry = dict[str, JsonValue]

OUTCOMES = frozenset({"expected", "unexpected", "flaky", "skipped"})
# The outcome of a test in several projects is the first of these that one of them had.
WORST = (Outcome.FAILED, Outcome.SKIPPED, Outcome.EXPECTED_FAILURE, Outcome.PASSED)
SEPARATOR = " > "
FOCUSED = "focused with '.only' is not allowed"


@dataclass(frozen=True, slots=True)
class PlaywrightTest:
    """One test in one project: its outcome there and the files it attached."""

    file: str
    title: tuple[str, ...]
    project: str
    outcome: Outcome
    attachments: tuple[tuple[str, str], ...] = ()

    @property
    def name(self) -> str:
        """The case name of the test, the same in every project."""
        return f"{self.file}::{SEPARATOR.join(self.title)}"

    def attached(self, name: str) -> tuple[str, ...]:
        """Return the paths of the attachments with this name."""
        return tuple(path for label, path in self.attachments if label == name)


@dataclass(frozen=True, slots=True)
class PlaywrightReport:
    """The declared projects, every test in every project, and the errors outside tests."""

    projects: tuple[str, ...]
    tests: tuple[PlaywrightTest, ...]
    errors: tuple[str, ...] = ()

    def by_name(self) -> dict[str, dict[str, PlaywrightTest]]:
        """Group the tests by case name, then by project."""
        grouped: dict[str, dict[str, PlaywrightTest]] = {}
        for test in self.tests:
            grouped.setdefault(test.name, {})[test.project] = test
        return grouped

    def results(self) -> SuiteResults:
        """Return one case per test with its worst outcome over the declared projects."""
        cases: list[CaseResult] = []
        for name, runs in self.by_name().items():
            outcomes = {runs[p].outcome if p in runs else Outcome.SKIPPED for p in self.projects}
            cases.append(CaseResult(name, next(o for o in WORST if o in outcomes)))
        return SuiteResults(tuple(cases), any(FOCUSED in error for error in self.errors))


def _fail(origin: str, problem: str) -> ReportError:
    return ReportError(f"{origin}: {problem}")


def _text(entry: Entry, key: str, origin: str) -> str:
    value = entry.get(key)
    if not isinstance(value, str) or not value:
        raise _fail(origin, f"'{key}' must be a non-empty string")
    return value


def _items(value: JsonValue, what: str, origin: str) -> list[JsonValue]:
    if not isinstance(value, list):
        raise _fail(origin, f"'{what}' must be a list")
    return value


def _objects(value: JsonValue, what: str, origin: str) -> Iterator[Entry]:
    for item in _items(value, what, origin):
        if not isinstance(item, dict):
            raise _fail(origin, f"every item of '{what}' must be an object")
        yield item


def _texts(value: JsonValue, what: str, origin: str) -> tuple[str, ...]:
    items = _items(value, what, origin)
    if not all(isinstance(item, str) for item in items):
        raise _fail(origin, f"'{what}' must be a list of strings")
    return tuple(item for item in items if isinstance(item, str))


def _outcome(entry: Entry, origin: str) -> Outcome:
    outcome = _text(entry, "outcome", origin)
    if outcome not in OUTCOMES:
        raise _fail(origin, f"unknown outcome '{outcome}'")
    if outcome == "skipped":
        return Outcome.SKIPPED
    if outcome != "expected":
        return Outcome.FAILED
    return (
        Outcome.PASSED if _text(entry, "expected", origin) == "passed" else Outcome.EXPECTED_FAILURE
    )


def _test(entry: Entry, projects: tuple[str, ...], origin: str) -> PlaywrightTest:
    project = _text(entry, "project", origin)
    if project not in projects:
        raise _fail(origin, f"a test ran in the undeclared project '{project}'")
    title = _texts(entry.get("title"), "title", origin)
    if not title:
        raise _fail(origin, "a test has an empty title")
    attachments = tuple(
        (_text(item, "name", origin), _text(item, "path", origin))
        for item in _objects(entry.get("attachments"), "attachments", origin)
    )
    file = _text(entry, "file", origin)
    return PlaywrightTest(file, title, project, _outcome(entry, origin), attachments)


def read_playwright(data: bytes, origin: str) -> PlaywrightReport:
    """Parse the reporter's results; raise ReportError when they are incomplete."""
    document = parse_json(data, origin)
    if not isinstance(document, dict):
        raise _fail(origin, "the results must be a JSON object")
    projects = _texts(document.get("projects"), "projects", origin)
    if not projects:
        raise _fail(origin, "the run declared no projects")
    tests = tuple(
        _test(item, projects, origin) for item in _objects(document.get("tests"), "tests", origin)
    )
    seen: set[tuple[str, str]] = set()
    for test in tests:
        if (test.name, test.project) in seen:
            raise _fail(origin, f"'{test.name}' is reported twice for '{test.project}'")
        seen.add((test.name, test.project))
    return PlaywrightReport(projects, tests, _texts(document.get("errors"), "errors", origin))


def parse_playwright(data: bytes, origin: str) -> SuiteResults:
    """Parse the reporter's results into one case per test over every project."""
    return read_playwright(data, origin).results()
