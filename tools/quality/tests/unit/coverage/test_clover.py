import re
from pathlib import Path

import pytest

from quality.boundedio import ReportError
from quality.coverage import Counter
from quality.coverage.clover import CloverFile, clover_files

SAMPLES = Path(__file__).parent / "samples"
ROOT = Path("/work/snapshot")
METRICS = '<metrics conditionals="2" coveredconditionals="1" statements="2" coveredstatements="1"/>'


def parse(data: str | bytes) -> dict[str, CloverFile]:
    raw = data.encode() if isinstance(data, str) else data
    return clover_files(raw, "clover.xml", root=ROOT, cwd=ROOT)


def clover(*files: str) -> str:
    return f'<coverage><project><package name="p">{"".join(files)}</package></project></coverage>'


def file(body: str, name: str = "src/a.php") -> str:
    return f'<file name="{name}">{body}</file>'


LINES = '<line num="3" type="stmt" count="2"/><line num="4" type="stmt" count="0"/>'


def test_a_real_phpunit_report_keeps_its_records_and_totals() -> None:
    # Real output of PHPUnit 13.4.3 with Xdebug 3.5.3, relocated to /work/snapshot.
    # The one-line constructor of Calc.php on line 9 has a method record and no
    # statement record, but its statement is in the totals.
    files = parse((SAMPLES / "phpunit.clover.xml").read_bytes())
    assert files == {
        "app/src/Calc.php": CloverFile(
            "app/src/Calc.php", {13: 2, 14: 1, 16: 1}, Counter(4, 4), Counter(6, 7)
        ),
        "app/src/Script.php": CloverFile(
            "app/src/Script.php", {9: 0, 12: 0}, Counter(0, 2), Counter(0, 0)
        ),
        "app/src/Shape.php": CloverFile("app/src/Shape.php", {}, Counter(0, 0), Counter(0, 0)),
        "app/src/Unused.php": CloverFile(
            "app/src/Unused.php", {9: 0}, Counter(0, 1), Counter(0, 1)
        ),
    }


def test_non_statement_lines_are_ignored() -> None:
    method = '<line num="2" type="method" name="f" count="0"/><line num="9" type="cond" count="1"/>'
    (found,) = parse(clover(file(method + LINES + METRICS))).values()
    assert found == CloverFile("src/a.php", {3: 2, 4: 0}, Counter(1, 2), Counter(1, 2))


def fails(data: str, message: str) -> None:
    with pytest.raises(ReportError, match=f"^{re.escape(message)}$"):
        parse(data)


@pytest.mark.parametrize(
    "data", ["<report/>", "<coverage/>", "<project><file/></project>"], ids=str
)
def test_documents_that_are_not_clover_are_rejected(data: str) -> None:
    fails(data, "clover.xml: not a Clover report (<coverage><project>)")


def test_files_need_names_and_appear_once() -> None:
    fails(clover('<file name="">' + METRICS + "</file>"), "clover.xml: <file> without a name")
    twice = clover(file(LINES + METRICS), file(LINES + METRICS, f"{ROOT}/src/a.php"))
    fails(twice, "clover.xml: src/a.php is reported twice")
    fails(
        clover(file(METRICS, "/elsewhere/a.php")),
        "report names a file outside the snapshot: /elsewhere/a.php",
    )


@pytest.mark.parametrize(
    ("body", "problem"),
    [
        (LINES, "<file> has no <metrics>"),
        (
            LINES + METRICS.replace('coveredstatements="1"', 'coveredstatements="3"'),
            "more covered statements than statements",
        ),
        (
            LINES + METRICS.replace('coveredconditionals="1"', 'coveredconditionals="3"'),
            "more covered conditionals than conditionals",
        ),
        (
            LINES + METRICS.replace(' conditionals="2"', ""),
            "<metrics> needs a non-negative integer 'conditionals'",
        ),
        (
            LINES + METRICS.replace('statements="2"', 'statements="-2"'),
            "<metrics> needs a non-negative integer 'statements'",
        ),
        (
            '<line num="x" type="stmt" count="1"/>' + METRICS,
            "<line> needs a non-negative integer 'num'",
        ),
        ('<line num="3" type="stmt"/>' + METRICS, "<line> needs a non-negative integer 'count'"),
        (
            '<line num="0" type="stmt" count="1"/>' + METRICS,
            "statement line 0 is invalid or repeated",
        ),
        (
            LINES + '<line num="3" type="stmt" count="1"/>' + METRICS,
            "statement line 3 is invalid or repeated",
        ),
    ],
)
def test_inconsistent_files_are_rejected(body: str, problem: str) -> None:
    fails(clover(file(body)), f"clover.xml [src/a.php]: {problem}")
