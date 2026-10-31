"""JUnit XML results, as written by pytest, Vitest, and PHPUnit."""

from __future__ import annotations

import re
from xml.etree.ElementTree import Element

from quality.boundedio import ReportError, parse_xml
from quality.testing.model import CaseResult, Outcome, SuiteResults

NUMBER = re.compile(r"[0-9]+")
MAX_DEPTH = 32
# A case with several markers counts as the most severe one.
MARKERS = (("error", Outcome.ERROR), ("failure", Outcome.FAILED), ("skipped", Outcome.SKIPPED))
# pytest reports an expected failure as a skip of this type.
XFAIL = "pytest.xfail"


def _outcome(case: Element) -> Outcome:
    tags = {child.tag for child in case}
    outcome = next((outcome for tag, outcome in MARKERS if tag in tags), Outcome.PASSED)
    skipped = case.find("skipped")
    if outcome is Outcome.SKIPPED and skipped is not None and skipped.get("type") == XFAIL:
        return Outcome.EXPECTED_FAILURE
    return outcome


def _case(case: Element, origin: str) -> CaseResult:
    name = case.get("name")
    if not name:
        msg = f"{origin}: <testcase> without a name"
        raise ReportError(msg)
    group = case.get("classname")
    return CaseResult(f"{group}::{name}" if group else name, _outcome(case))


def _check_declared(element: Element, cases: list[CaseResult], origin: str) -> None:
    results = SuiteResults(tuple(cases))
    actual = {
        "tests": len(cases),
        "failures": results.count(Outcome.FAILED),
        "errors": results.count(Outcome.ERROR),
        "skipped": results.count(Outcome.SKIPPED, Outcome.EXPECTED_FAILURE),
    }
    for key, value in actual.items():
        declared = element.get(key)
        if declared is None:
            continue
        if NUMBER.fullmatch(declared) is None or int(declared) != value:
            name = element.get("name") or element.tag
            msg = f"{origin}: '{name}' declares {key}={declared!r}, but the report has {value}"
            raise ReportError(msg)


def _suite(element: Element, origin: str, depth: int) -> list[CaseResult]:
    if depth > MAX_DEPTH:
        msg = f"{origin}: test suites nested deeper than {MAX_DEPTH} levels"
        raise ReportError(msg)
    cases = [_case(case, origin) for case in element.findall("testcase")]
    for child in element.findall("testsuite"):
        cases.extend(_suite(child, origin, depth + 1))
    _check_declared(element, cases, origin)
    return cases


def parse_junit(data: bytes, origin: str) -> SuiteResults:
    """Parse results and check every declared count against the listed test cases."""
    document = parse_xml(data, origin)
    if document.tag not in {"testsuites", "testsuite"}:
        msg = f"{origin}: not a JUnit report (<{document.tag}>)"
        raise ReportError(msg)
    return SuiteResults(tuple(_suite(document, origin, 0)))
