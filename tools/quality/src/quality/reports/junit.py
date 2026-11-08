"""JUnit XML from normalized results: one test case per result, with its outcome."""

from __future__ import annotations

from xml.etree.ElementTree import Element, SubElement, tostring

from quality.testing import Outcome, Suite, SuiteResults

# JUnit has no expected failure; it is reported as a skip with its own type.
CHILDREN = {
    Outcome.FAILED: ("failure", "failed"),
    Outcome.ERROR: ("error", "error"),
    Outcome.SKIPPED: ("skipped", "skipped"),
    Outcome.EXPECTED_FAILURE: ("skipped", "expected-failure"),
}


def _split(name: str, suite: Suite) -> tuple[str, str]:
    owner, separator, case = name.rpartition("::")
    return (owner, case) if separator and owner else (str(suite), name)


def junit_document(component: str, suite: Suite, results: SuiteResults) -> bytes:
    """Render one suite run as a JUnit `testsuites` document."""
    counts = {
        "tests": str(len(results.cases)),
        "failures": str(results.count(Outcome.FAILED)),
        "errors": str(results.count(Outcome.ERROR)),
        "skipped": str(results.count(Outcome.SKIPPED, Outcome.EXPECTED_FAILURE)),
    }
    root = Element("testsuites", counts)
    body = SubElement(root, "testsuite", {"name": f"{component} {suite}", **counts})
    for result in results.cases:
        classname, name = _split(result.name, suite)
        case = SubElement(body, "testcase", {"classname": classname, "name": name})
        child = CHILDREN.get(result.outcome)
        if child is not None:
            tag, kind = child
            SubElement(case, tag, {"type": kind, "message": kind})
    return tostring(root, encoding="utf-8", xml_declaration=True) + b"\n"
