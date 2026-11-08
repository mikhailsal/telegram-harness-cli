import re
from pathlib import Path

import pytest

from quality.boundedio import ReportError
from quality.coverage import (
    EMPTY,
    Counter,
    CoverageReport,
    FileCoverage,
    parse_coverage_json,
    parse_lcov,
)

SAMPLES = Path(__file__).parent / "samples"
ROOT = Path("/work/snapshot")


def parse(text: str | bytes, *, measured: bool = True, cwd: Path = ROOT) -> CoverageReport:
    data = text.encode() if isinstance(text, str) else text
    return parse_lcov(data, "lcov.info", root=ROOT, cwd=cwd, branches_measured=measured)


def lines(*records: str) -> str:
    return "\n".join(records) + "\n"


def test_coverage_py_lcov_matches_its_json_report() -> None:
    # Both samples are real output of the same coverage.py 7.16.2 run with --cov-branch.
    cwd = ROOT / "lib"
    lcov = parse((SAMPLES / "coverage-py.lcov").read_bytes(), cwd=cwd)
    data = (SAMPLES / "coverage-py.json").read_bytes()
    native = parse_coverage_json(data, "coverage-py.json", root=ROOT, cwd=cwd)
    assert dict(lcov.files) == dict(native.files)
    assert lcov.files["lib/pkg/m.py"].branches == Counter(1, 6)
    assert lcov.files["lib/pkg/__init__.py"].lines == EMPTY


def test_a_real_vitest_v8_tracefile_is_normalized() -> None:
    # Real output of Vitest 3.2.4 with the V8 provider; src/unused.ts is never imported.
    report = parse((SAMPLES / "vitest-v8.lcov").read_bytes(), cwd=ROOT / "web")
    assert dict(report.files) == {
        "web/src/m.ts": FileCoverage(
            "web/src/m.ts", frozenset(range(1, 7)), frozenset(range(1, 7)), Counter(1, 3)
        ),
        "web/src/unused.ts": FileCoverage(
            "web/src/unused.ts", frozenset({1, 2, 3}), frozenset(), Counter(0, 1)
        ),
    }


def test_the_adapter_states_whether_branches_were_measured() -> None:
    assert not parse(lines("SF:a.ts", "DA:1,1", "end_of_record"), measured=False).branches_measured
    assert parse("").files == {}


def test_repeated_blocks_of_a_file_are_merged() -> None:
    report = parse(
        lines(
            "TN:first",
            "SF:pkg/a.ts",
            "DA:1,0",
            "DA:2,1",
            "BRDA:1,0,0,0",
            "end_of_record",
            "TN:second",
            "SF:./pkg/a.ts",
            "DA:1,1",
            "DA:3,0",
            "BRDA:1,0,0,2",
            "BRDA:1,0,1,-",
            "end_of_record",
        )
    )
    assert dict(report.files) == {
        "pkg/a.ts": FileCoverage("pkg/a.ts", frozenset({1, 2, 3}), frozenset({1, 2}), Counter(1, 2))
    }


def test_branch_expressions_may_contain_commas() -> None:
    report = parse(
        lines("SF:a.ts", "DA:3,1", "BRDA:3,0,(a, b),1", "BRDA:3,0,(a, c),0", "end_of_record")
    )
    assert report.files["a.ts"].branches == Counter(1, 2)


def test_ignored_records_blank_lines_and_crlf_are_accepted() -> None:
    text = "\r\n".join(
        [
            "VER:2.0",
            "TN:",
            "SF:a.ts",
            "FN:1,3,f",
            "FNL:0,1,3",
            "FNA:0,1,f",
            "FNDA:1,f",
            "FNF:1",
            "FNH:1",
            "",
            "DA:1,4,checksum",
            "LF:1",
            "LH:1",
            "BRF:0",
            "BRH:0",
            "end_of_record",
        ]
    )
    assert parse(text).files["a.ts"] == FileCoverage("a.ts", frozenset({1}), frozenset({1}))


def fails(text: str | bytes, message: str) -> None:
    with pytest.raises(ReportError, match=f"^{re.escape(message)}"):
        parse(text)


def test_non_utf8_tracefiles_are_rejected() -> None:
    fails(b"SF:a.ts\nDA:1,1\n\xff\nend_of_record\n", "lcov.info: not UTF-8: ")


@pytest.mark.parametrize(
    ("records", "message"),
    [
        (["SF:a.ts", "garbage"], "lcov.info:2: not an LCOV record: 'garbage'"),
        (["SF:a.ts", "XYZ:1"], "lcov.info:2: unknown LCOV record 'XYZ'"),
        (["DA:1,1"], "lcov.info:1: record outside an SF block"),
        (["end_of_record"], "lcov.info:1: record outside an SF block"),
        (["SF:a.ts", "SF:b.ts"], "lcov.info:2: SF before the previous end_of_record"),
        (["SF:a.ts", "DA:1"], "lcov.info:2: DA needs a line, a hit count, and an optional"),
        (["SF:a.ts", "DA:1,1,c,d"], "lcov.info:2: DA needs a line, a hit count, and an optional"),
        (["SF:a.ts", "DA:0,1"], "lcov.info:2: line numbers start at 1"),
        (["SF:a.ts", "DA:x,1"], "lcov.info:2: 'x' is not a non-negative integer"),
        (["SF:a.ts", "DA:1,-1"], "lcov.info:2: '-1' is not a non-negative integer"),
        (["SF:a.ts", "BRDA:1,0"], "lcov.info:2: BRDA needs a line, a block, a branch, and a"),
        (["SF:a.ts", "BRDA:1,0,1"], "lcov.info:2: BRDA needs a line, a block, a branch, and a"),
        (["SF:a.ts", "BRDA:1,0,0,x"], "lcov.info:2: 'x' is not a non-negative integer"),
        (["SF:a.ts", "BRDA:0,0,0,1"], "lcov.info:2: line numbers start at 1"),
        (["SF:a.ts", "LF:x"], "lcov.info:2: 'x' is not a non-negative integer"),
        (["SF:../a.ts"], "report names a file outside the snapshot: ../a.ts"),
    ],
)
def test_malformed_records_are_rejected(records: list[str], message: str) -> None:
    fails(lines(*records, "end_of_record"), message)


def test_truncated_tracefiles_are_rejected() -> None:
    fails(lines("SF:a.ts", "DA:1,1"), "lcov.info: truncated report; the last record has no")


@pytest.mark.parametrize(
    ("declared", "actual"),
    [("LF:3", 2), ("LH:2", 1), ("BRF:1", 2), ("BRH:0", 1)],
)
def test_declared_totals_must_match_the_records(declared: str, actual: int) -> None:
    records = ["SF:a.ts", "DA:1,1", "DA:2,0", "BRDA:1,0,0,1", "BRDA:1,0,1,0", declared]
    message = f"lcov.info:7: {declared} disagrees with the records ({actual})"
    fails(lines(*records, "end_of_record"), message)
