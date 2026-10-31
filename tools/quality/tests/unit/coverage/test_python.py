import json
import re
from pathlib import Path
from typing import Any

import pytest

from quality.boundedio import ReportError
from quality.coverage import EMPTY, Counter, CoverageReport, FileCoverage, parse_coverage_json

SAMPLES = Path(__file__).parent / "samples"
ROOT = Path("/work/snapshot")
A = "pkg/a.py"
SUMMARY_KEYS = ("num_statements", "covered_lines", "num_branches", "covered_branches")
DELETE = object()

# samples/coverage-py.json is real output of pytest-cov with coverage.py 7.16.2 and
# --cov-branch for a package where m.py runs partly and unused.py is never imported.
REAL = {
    "lib/pkg/__init__.py": FileCoverage("lib/pkg/__init__.py", frozenset(), frozenset(), EMPTY),
    "lib/pkg/m.py": FileCoverage(
        "lib/pkg/m.py",
        frozenset({1, 2, 3, 4, 5, 6, 7, 10, 11}),
        frozenset({1, 2, 3, 10}),
        Counter(1, 6),
    ),
    "lib/pkg/unused.py": FileCoverage(
        "lib/pkg/unused.py", frozenset({1, 2, 3, 4}), frozenset(), Counter(0, 2)
    ),
}


def entry(
    executed: list[int],
    missing: list[int],
    taken: list[list[int]] | None = None,
    missed: list[list[int]] | None = None,
) -> dict[str, Any]:
    taken, missed = taken or [], missed or []
    counts = (len(executed) + len(missing), len(executed), len(taken) + len(missed), len(taken))
    return {
        "executed_lines": executed,
        "missing_lines": missing,
        "executed_branches": taken,
        "missing_branches": missed,
        "summary": dict(zip(SUMMARY_KEYS, counts, strict=True)),
    }


def document(files: dict[str, dict[str, Any]], *, branch: object = True) -> dict[str, Any]:
    totals = {key: sum(f["summary"][key] for f in files.values()) for key in SUMMARY_KEYS}
    return {"meta": {"format": 3, "branch_coverage": branch}, "files": files, "totals": totals}


def valid() -> dict[str, Any]:
    return document({A: entry([1, 2], [3], [[1, 2]], [[1, -1]])})


def altered(path: tuple[str, ...], value: object) -> dict[str, Any]:
    doc = valid()
    target: Any = doc
    for key in path[:-1]:
        target = target[key]
    if value is DELETE:
        del target[path[-1]]
    else:
        target[path[-1]] = value
    return doc


def parse(doc: object, cwd: Path = ROOT) -> CoverageReport:
    return parse_coverage_json(json.dumps(doc).encode(), "cov.json", root=ROOT, cwd=cwd)


def test_a_real_coverage_py_report_is_normalized() -> None:
    data = (SAMPLES / "coverage-py.json").read_bytes()
    report = parse_coverage_json(data, "coverage-py.json", root=ROOT, cwd=ROOT / "lib")
    assert report.branches_measured
    assert dict(report.files) == REAL


def test_valid_documents_keep_lines_and_branch_arcs() -> None:
    report = parse(valid())
    assert dict(report.files) == {
        A: FileCoverage(A, frozenset({1, 2, 3}), frozenset({1, 2}), Counter(1, 2))
    }


@pytest.mark.parametrize("branch", [False, "true", DELETE])
def test_reports_without_branch_measurement_say_so(branch: object) -> None:
    files = {A: {"executed_lines": [1], "missing_lines": [], "summary": entry([1], [])["summary"]}}
    doc = document(files, branch=branch)
    if branch is DELETE:
        del doc["meta"]["branch_coverage"]
    del doc["totals"]["num_branches"]
    report = parse(doc)
    assert not report.branches_measured
    assert report.files[A].branches == EMPTY


def fails(doc: object, message: str) -> None:
    with pytest.raises(ReportError, match=f"^{re.escape(message)}$"):
        parse(doc)


def test_documents_that_are_not_objects_are_rejected() -> None:
    fails([], "cov.json: the report is not an object")


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("meta", "format"), 2, "unsupported format 2; expected 3"),
        (("meta",), DELETE, "meta is not an object"),
        (("files",), [], "files is not an object"),
        (("totals",), DELETE, "totals is not an object"),
        (("files", A), "x", f"{A} is not an object"),
        (
            ("totals", "num_statements"),
            99,
            "num_statements disagrees with the listed lines or branches",
        ),
        (
            ("totals", "covered_lines"),
            0,
            "covered_lines disagrees with the listed lines or branches",
        ),
        (("totals", "num_branches"), 0, "num_branches disagrees with the listed lines or branches"),
        (
            ("totals", "covered_branches"),
            5,
            "covered_branches disagrees with the listed lines or branches",
        ),
    ],
)
def test_malformed_documents_are_rejected(
    path: tuple[str, ...], value: object, message: str
) -> None:
    fails(altered(path, value), f"cov.json: {message}")


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("summary", DELETE, "summary is not an object"),
        ("executed_lines", "1", "executed_lines is not a list"),
        ("executed_lines", [1, True], "executed_lines is not a non-negative integer"),
        ("missing_lines", [-3], "missing_lines is not a non-negative integer"),
        ("missing_lines", [0], "missing_lines has invalid or repeated line numbers"),
        ("missing_lines", [3, 3], "missing_lines has invalid or repeated line numbers"),
        ("missing_lines", [2], "a line is both executed and missing"),
        ("executed_branches", {}, "executed_branches is not a list"),
        ("missing_branches", [[1, -1], [1, -1]], "missing_branches has repeated arcs"),
        ("missing_branches", [[1, 2]], "an arc is both executed and missing"),
    ],
)
def test_inconsistent_files_are_rejected(key: str, value: object, message: str) -> None:
    fails(altered(("files", A, key), value), f"cov.json [{A}]: {message}")


@pytest.mark.parametrize("arc", [[1], [1, 2, 3], [True, 1], [1, True], ["1", 2], 1, None], ids=str)
def test_arcs_must_be_pairs_of_line_numbers(arc: object) -> None:
    message = "executed_branches has an entry that is not a pair of line numbers"
    fails(altered(("files", A, "executed_branches"), [arc]), f"cov.json [{A}]: {message}")


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("num_statements", 4, "num_statements disagrees with the listed lines or branches"),
        ("covered_lines", True, "covered_lines is not a non-negative integer"),
        ("covered_lines", 1, "covered_lines disagrees with the listed lines or branches"),
        ("num_branches", 3, "num_branches disagrees with the listed lines or branches"),
        ("covered_branches", DELETE, "covered_branches is not a non-negative integer"),
    ],
)
def test_summaries_must_agree_with_the_listed_lines(key: str, value: object, message: str) -> None:
    fails(altered(("files", A, "summary", key), value), f"cov.json [{A}]: {message}")


def test_a_file_reported_twice_is_rejected() -> None:
    doc = document({A: entry([1], []), f"./{A}": entry([2], [])})
    fails(doc, f"cov.json: {A} is reported twice")


def test_files_outside_the_snapshot_are_rejected() -> None:
    fails(
        document({"../x.py": entry([1], [])}), "report names a file outside the snapshot: ../x.py"
    )
