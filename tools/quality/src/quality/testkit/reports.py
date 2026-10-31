"""Builders for native test reports: coverage.py JSON and JUnit XML written by fake runners."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from xml.etree.ElementTree import Element, SubElement, tostring

from quality.testing import Outcome

MARKERS = {Outcome.FAILED: "failure", Outcome.ERROR: "error", Outcome.SKIPPED: "skipped"}
DECLARED = {
    "failures": (Outcome.FAILED,),
    "errors": (Outcome.ERROR,),
    "skipped": (Outcome.SKIPPED, Outcome.EXPECTED_FAILURE),
}


@dataclass(frozen=True, slots=True)
class FileSpec:
    """One file of a coverage report: its lines and its taken and total branches."""

    executable: frozenset[int]
    covered: frozenset[int]
    branches: tuple[int, int] = (0, 0)

    @classmethod
    def first(cls, executable: int, covered: int, branches: tuple[int, int] = (0, 0)) -> FileSpec:
        """Return a file of `executable` lines whose first `covered` lines ran."""
        lines = frozenset(range(1, executable + 1))
        return cls(lines, frozenset(range(1, covered + 1)), branches)


def _entry(spec: FileSpec, *, branch: bool) -> dict[str, object]:
    executed = sorted(spec.covered)
    missing = sorted(spec.executable - spec.covered)
    summary: dict[str, int] = {
        "num_statements": len(spec.executable),
        "covered_lines": len(executed),
    }
    entry: dict[str, object] = {
        "executed_lines": executed,
        "missing_lines": missing,
        "summary": summary,
    }
    if branch:
        taken, possible = spec.branches
        arcs = [[number, -number] for number in range(1, possible + 1)]
        entry["executed_branches"] = arcs[:taken]
        entry["missing_branches"] = arcs[taken:]
        summary.update(num_branches=possible, covered_branches=taken)
    return entry


def coverage_json(files: Mapping[str, FileSpec], *, branch: bool = True) -> bytes:
    """Render a coverage.py format 3 JSON report with consistent summaries and totals."""
    entries = {name: _entry(spec, branch=branch) for name, spec in files.items()}
    totals = {
        "num_statements": sum(len(spec.executable) for spec in files.values()),
        "covered_lines": sum(len(spec.covered) for spec in files.values()),
    }
    if branch:
        totals["num_branches"] = sum(spec.branches[1] for spec in files.values())
        totals["covered_branches"] = sum(spec.branches[0] for spec in files.values())
    meta = {"format": 3, "branch_coverage": branch}
    return json.dumps({"meta": meta, "files": entries, "totals": totals}).encode()


def junit_xml(cases: Iterable[tuple[str, Outcome]]) -> bytes:
    """Render a JUnit XML report with one suite and consistent declared counts."""
    listed = list(cases)
    root = Element("testsuites")
    suite = SubElement(root, "testsuite", name="fake")
    for name, outcome in listed:
        case = SubElement(suite, "testcase", classname="tests", name=name)
        if outcome in MARKERS:
            SubElement(case, MARKERS[outcome])
        elif outcome is Outcome.EXPECTED_FAILURE:
            SubElement(case, "skipped", type="pytest.xfail")
    suite.set("tests", str(len(listed)))
    for key, outcomes in DECLARED.items():
        suite.set(key, str(sum(1 for _, found in listed if found in outcomes)))
    return tostring(root, encoding="utf-8")
