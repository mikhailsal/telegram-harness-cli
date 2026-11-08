import json
import re
from pathlib import Path

import pytest

from quality.boundedio import JsonValue, ReportError
from quality.coverage import Counter, CoverageReport, FileCoverage, JsonlReport, parse_phpunit

SAMPLES = Path(__file__).parent / "samples"
JSONL = SAMPLES / "phpunit-jsonl"
ROOT = Path("/work/snapshot")
CWD = ROOT / "app"
CLOVER = (SAMPLES / "phpunit.clover.xml").read_bytes()
SOURCE = "/work/snapshot/app/src"


def sample() -> JsonlReport:
    return JsonlReport(
        *((JSONL / name).read_bytes() for name in ("meta.json", "coverage.jsonl", "tests.jsonl"))
    )


def parse(jsonl: JsonlReport | None = None, clover: bytes = CLOVER) -> CoverageReport:
    return parse_phpunit(clover, jsonl or sample(), root=ROOT, cwd=CWD)


def records(*items: object) -> bytes:
    return "".join(json.dumps(item) + "\n" for item in items).encode()


def meta(**fields: JsonValue) -> bytes:
    document: dict[str, JsonValue] = {
        "schemaVersion": 1,
        "sourceRoot": SOURCE,
        "branchCoverage": True,
        "files": 1,
        "executableLines": 2,
        "executedLines": 1,
    }
    document.update(fields)
    return json.dumps(document).encode()


FILE = (
    '<file name="/work/snapshot/app/src/A.php">{lines}<metrics conditionals="{branches}" '
    'coveredconditionals="1" statements="{total}" coveredstatements="{covered}"/></file>'
)


def clover(
    lines: str = '<line num="3" type="stmt" count="1"/><line num="5" type="stmt" count="0"/>',
    *,
    total: int = 2,
    covered: int = 1,
    branches: int = 2,
) -> bytes:
    body = FILE.format(lines=lines, total=total, covered=covered, branches=branches)
    return f"<coverage><project>{body}</project></coverage>".encode()


A = {"file": "A.php", "executable": 2, "executed": 1, "uncovered": ["5"]}
TEST = {"test": "T::t", "covers": {"A.php": ["3"]}}
TWICE = "app/src/A.php is reported twice or with more executed than executable lines"
SCHEMA = "meta.json: not a JSONL coverage report of schema version 1"
REQUIRED = "meta.json: 'sourceRoot' and 'branchCoverage' are required"
TOTALS = "meta.json: the totals disagree with coverage.jsonl"
NEEDS = "tests.jsonl:1: a record needs a 'test' and its 'covers'"
COVERED = "tests.jsonl [app/src/A.php]: the covered lines disagree with coverage.jsonl"
CLOVER_TOTALS = "clover.xml [app/src/A.php]: the statement totals disagree with the JSONL report"
LIMIT = "more lines than the file's 2 executable lines"
STATEMENT = "clover.xml [app/src/A.php]: statement line {} disagrees with the JSONL report"


def small(
    meta_data: bytes | None = None,
    coverage: tuple[object, ...] = (A,),
    tests: tuple[object, ...] = (TEST,),
) -> JsonlReport:
    return JsonlReport(meta_data or meta(), records(*coverage), records(*tests))


def test_a_real_report_takes_the_lines_from_jsonl_and_the_branches_from_clover() -> None:
    # Real output of PHPUnit 13.4.3 with php-code-coverage 14.4.1 and Xdebug 3.5.3.
    report = parse()
    assert report.branches_measured
    assert dict(report.files) == {
        # Line 9 is the one-line constructor that Clover has no statement record of.
        "app/src/Calc.php": FileCoverage(
            "app/src/Calc.php",
            frozenset({9, 13, 14, 16}),
            frozenset({9, 13, 14, 16}),
            Counter(6, 7),
        ),
        # Never loaded: its lines are known, its branches are not.
        "app/src/Script.php": FileCoverage(
            "app/src/Script.php", frozenset({9, 12}), frozenset(), Counter(0, 0)
        ),
        # No executable line: JSONL leaves it out and Clover keeps it.
        "app/src/Shape.php": FileCoverage("app/src/Shape.php", frozenset(), frozenset()),
        "app/src/Unused.php": FileCoverage(
            "app/src/Unused.php", frozenset({9}), frozenset(), Counter(0, 1)
        ),
    }


def test_a_small_report_is_merged() -> None:
    report = parse(small(), clover())
    assert dict(report.files) == {
        "app/src/A.php": FileCoverage(
            "app/src/A.php", frozenset({3, 5}), frozenset({3}), Counter(1, 2)
        )
    }


def test_ranges_and_several_tests_add_up() -> None:
    a = {"file": "A.php", "executable": 5, "executed": 4, "uncovered": ["8"]}
    tests = (
        {"test": "T::a", "covers": {"A.php": ["3-4"]}},
        {"test": "T::b", "covers": {"A.php": ["4-6"]}},
    )
    lines = "".join(f'<line num="{n}" type="stmt" count="{int(n != 8)}"/>' for n in (3, 4, 5, 6, 8))
    report = parse(
        small(meta(executableLines=5, executedLines=4), (a,), tests),
        clover(lines, total=5, covered=4),
    )
    assert report.files["app/src/A.php"].covered == frozenset({3, 4, 5, 6})
    assert report.files["app/src/A.php"].executable == frozenset({3, 4, 5, 6, 8})


def test_a_run_without_branch_collection_measured_no_branches() -> None:
    assert not parse(small(meta(branchCoverage=False)), clover()).branches_measured
    empty = small(meta(branchCoverage=False, files=0, executableLines=0, executedLines=0), (), ())
    nothing = b"<coverage><project/></coverage>"
    assert parse(empty, nothing).branches_measured


def fails(jsonl: JsonlReport, message: str, data: bytes | None = None) -> None:
    with pytest.raises(ReportError, match=f"^{re.escape(message)}$"):
        parse(jsonl, data or clover())


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"[]", SCHEMA),
        (meta(schemaVersion=2), SCHEMA),
        (meta(schemaVersion=True), SCHEMA),
        (meta(sourceRoot=""), REQUIRED),
        (meta(branchCoverage="yes"), REQUIRED),
        (meta(executedLines=-1), "meta.json: 'executedLines' must be a non-negative integer"),
        (meta(executedLines=3), "meta.json: more executed than executable lines"),
        (meta(files=True), "meta.json: 'files' must be a non-negative integer"),
        (meta(files=2), TOTALS),
        (meta(executedLines=2), TOTALS),
    ],
    ids=["list", "schema", "bool", "root", "flag", "sign", "more", "files", "count", "lines"],
)
def test_a_bad_meta_file_is_rejected(data: bytes, message: str) -> None:
    fails(small(data), message)


@pytest.mark.parametrize(
    ("coverage", "message"),
    [
        (("[1]",), "coverage.jsonl:1: a record must be a JSON object"),
        ({"executable": 2, "executed": 1}, "coverage.jsonl:1: a record needs a 'file'"),
        ({**A, "executable": "2"}, "coverage.jsonl:1: 'executable' must be a non-negative integer"),
        ({**A, "executed": 3}, f"coverage.jsonl:1: {TWICE}"),
        ({**A, "uncovered": "5"}, "coverage.jsonl:1: line ranges must be a list"),
        ({**A, "uncovered": ["0"]}, "coverage.jsonl:1: '0' is not a line range"),
        ({**A, "uncovered": ["6-5"]}, "coverage.jsonl:1: '6-5' is not a line range"),
        ({**A, "uncovered": [5]}, "coverage.jsonl:1: 5 is not a line range"),
        ({**A, "uncovered": ["5-7"]}, f"coverage.jsonl:1: {LIMIT}"),
        (
            {**A, "file": "../../../../elsewhere/A.php"},
            f"report names a file outside the snapshot: {SOURCE}/../../../../elsewhere/A.php",
        ),
    ],
    ids=["object", "file", "type", "more", "list", "zero", "back", "number", "limit", "out"],
)
def test_a_bad_coverage_record_is_rejected(coverage: JsonValue, message: str) -> None:
    if isinstance(coverage, tuple):
        jsonl = JsonlReport(meta(), b"[1]\n", records(TEST))
    else:
        jsonl = small(coverage=(coverage,))
    fails(jsonl, message)


def test_a_file_reported_twice_is_rejected() -> None:
    fails(small(coverage=(A, A)), f"coverage.jsonl:2: {TWICE}")


def test_blank_lines_are_skipped_and_malformed_ones_rejected() -> None:
    jsonl = small()
    assert parse(
        JsonlReport(jsonl.meta, b"\n" + jsonl.coverage + b"\n", jsonl.tests), clover()
    ).files
    fails(
        JsonlReport(jsonl.meta, b"{", jsonl.tests),
        "malformed JSON in coverage.jsonl:1: Expecting property name enclosed in double quotes: "
        "line 1 column 2 (char 1)",
    )


@pytest.mark.parametrize(
    ("tests", "message"),
    [
        ({"covers": {"A.php": ["3"]}}, NEEDS),
        ({"test": "T::t", "covers": ["A.php"]}, NEEDS),
        (
            {"test": "T::t", "covers": {"B.php": ["3"]}},
            "tests.jsonl:1: app/src/B.php has no record in coverage.jsonl",
        ),
        ({"test": "T::t", "covers": {"A.php": ["5"]}}, COVERED),
        ({"test": "T::t", "covers": {"A.php": []}}, COVERED),
        ({"test": "T::t", "covers": {"A.php": ["3-5"]}}, f"tests.jsonl:1: {LIMIT}"),
    ],
    ids=["test", "covers", "unknown", "uncovered", "missing", "limit"],
)
def test_a_bad_test_record_is_rejected(tests: JsonValue, message: str) -> None:
    fails(small(tests=(tests,)), message)


def test_lines_that_are_neither_covered_nor_uncovered_are_rejected() -> None:
    a = {**A, "executable": 3}
    fails(
        small(meta(executableLines=3), (a,)),
        "coverage.jsonl [app/src/A.php]: the executable lines disagree with the uncovered ones",
    )


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (clover(total=3), CLOVER_TOTALS),
        (clover(covered=0), CLOVER_TOTALS),
        (clover('<line num="3" type="stmt" count="0"/>'), STATEMENT.format(3)),
        (clover('<line num="4" type="stmt" count="1"/>'), STATEMENT.format(4)),
        (b"<coverage><project/></coverage>", "clover.xml: app/src/A.php are missing"),
    ],
    ids=["total", "covered", "state", "line", "missing"],
)
def test_clover_must_agree_with_jsonl(data: bytes, message: str) -> None:
    fails(small(), message, data)


def test_a_clover_file_that_jsonl_lacks_must_have_no_statements() -> None:
    fails(small(meta(files=0, executableLines=0, executedLines=0), (), ()), CLOVER_TOTALS)
