from pathlib import Path

from quality.boundedio import parse_xml
from quality.coverage import Counter, CoverageReport, FileCoverage, parse_lcov
from quality.reports import cobertura_document, junit_document, lcov_document
from quality.testing import CaseResult, Outcome, Suite, SuiteResults, parse_junit

ROOT = Path("/snapshot")
REPORT = CoverageReport(
    {
        "src/b.py": FileCoverage(
            "src/b.py", frozenset({1, 2, 4}), frozenset({1, 4}), Counter(1, 2)
        ),
        "src/a.py": FileCoverage("src/a.py", frozenset({3}), frozenset(), Counter(0, 0)),
        "src/c.php": FileCoverage(
            "src/c.php", frozenset({1}), frozenset({1}), Counter(0, 0), branches_measured=False
        ),
    },
    branches_measured=True,
)
RESULTS = SuiteResults(
    (
        CaseResult("tests/test_a.py::test_pass", Outcome.PASSED),
        CaseResult("tests/test_a.py::test_fail", Outcome.FAILED),
        CaseResult("tests/test_a.py::Case::test_error", Outcome.ERROR),
        CaseResult("skipped without owner", Outcome.SKIPPED),
        CaseResult("tests/test_a.py::test_xfail", Outcome.EXPECTED_FAILURE),
    )
)


def test_junit_keeps_every_case_and_its_outcome() -> None:
    document = junit_document("app", Suite.UNIT, RESULTS)
    root = parse_xml(document, "junit")
    assert {key: root.get(key) for key in ("tests", "failures", "errors", "skipped")} == {
        "tests": "5",
        "failures": "1",
        "errors": "1",
        "skipped": "2",
    }
    cases = [(case.get("classname"), case.get("name")) for case in root.iter("testcase")]
    assert cases[2:4] == [
        ("tests/test_a.py::Case", "test_error"),
        ("unit", "skipped without owner"),
    ]
    kinds = [child.get("type") for case in root.iter("testcase") for child in case]
    assert kinds == ["failed", "error", "skipped", "expected-failure"]
    outcomes = [case.outcome for case in parse_junit(document, "junit").cases]
    assert outcomes[:4] == [Outcome.PASSED, Outcome.FAILED, Outcome.ERROR, Outcome.SKIPPED]


def test_lcov_reports_the_lines_of_each_file_in_path_order() -> None:
    document = lcov_document(REPORT)
    assert document.startswith("TN:\nSF:src/a.py\nDA:3,0\nLF:1\nLH:0\nend_of_record\n")
    parsed = parse_lcov(
        document.encode(), "lcov", root=ROOT, cwd=ROOT, branches_measured=False
    ).files
    assert {path: (entry.executable, entry.covered) for path, entry in parsed.items()} == {
        path: (entry.executable, entry.covered) for path, entry in REPORT.files.items()
    }


def test_cobertura_carries_exact_counts_and_relative_file_names() -> None:
    root = parse_xml(cobertura_document("app", REPORT), "cobertura")
    assert {key: root.get(key) for key in root.attrib if "-" in key} == {
        "line-rate": "0.6000",
        "branch-rate": "0.5000",
        "lines-valid": "5",
        "lines-covered": "3",
        "branches-valid": "2",
        "branches-covered": "1",
    }
    classes = {unit.get("filename"): unit for unit in root.iter("class")}
    assert list(classes) == ["src/a.py", "src/b.py", "src/c.php"]
    assert classes["src/a.py"].get("branch-rate") == "1"
    hits = [(line.get("number"), line.get("hits")) for line in classes["src/b.py"].iter("line")]
    assert hits == [("1", "1"), ("2", "0"), ("4", "1")]
    package = next(root.iter("package"))
    assert (package.get("name"), package.get("line-rate")) == ("app", "0.6000")
