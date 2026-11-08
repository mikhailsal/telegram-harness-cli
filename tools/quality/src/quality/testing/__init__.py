"""Testing: normalized results, suite runs, static requests, and the results parsers."""

from quality.testing.dartjson import parse_dart_json
from quality.testing.junit import parse_junit
from quality.testing.model import (
    ACCESSIBILITY_SCAN,
    CaseResult,
    Outcome,
    ResultsFormat,
    StaticRequest,
    Suite,
    SuiteRequest,
    SuiteResults,
    SuiteRun,
    suite_roots,
)
from quality.testing.playwright import (
    PlaywrightReport,
    PlaywrightTest,
    parse_playwright,
    read_playwright,
)

__all__ = [
    "ACCESSIBILITY_SCAN",
    "CaseResult",
    "Outcome",
    "PlaywrightReport",
    "PlaywrightTest",
    "ResultsFormat",
    "StaticRequest",
    "Suite",
    "SuiteRequest",
    "SuiteResults",
    "SuiteRun",
    "parse_dart_json",
    "parse_junit",
    "parse_playwright",
    "read_playwright",
    "suite_roots",
]
