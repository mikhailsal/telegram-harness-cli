from pathlib import Path

import pytest

from quality.boundedio import ReportError
from quality.testing import CaseResult, Outcome, SuiteResults, parse_junit
from quality.testing.junit import MAX_DEPTH

SAMPLES = Path(__file__).parent / "samples"

PASSED, FAILED, ERROR, SKIPPED = Outcome.PASSED, Outcome.FAILED, Outcome.ERROR, Outcome.SKIPPED


def sample(name: str) -> SuiteResults:
    return parse_junit((SAMPLES / name).read_bytes(), name)


def outcomes(results: SuiteResults) -> list[tuple[str, Outcome]]:
    return [(case.name, case.outcome) for case in results.cases]


def test_a_real_pytest_report_is_parsed() -> None:
    results = sample("pytest.xml")
    group = "tests.test_sample"
    assert outcomes(results) == [
        (f"{group}::test_passes", PASSED),
        (f"{group}::test_fails", FAILED),
        (f"{group}::test_errors", ERROR),
        (f"{group}::test_skipped", SKIPPED),
        (f"{group}.TestGroup::test_method", PASSED),
        # pytest reports a teardown error as a second case with the same name.
        (f"{group}::test_fails_and_teardown_errors", FAILED),
        (f"{group}::test_fails_and_teardown_errors", ERROR),
    ]
    assert (results.executed, results.count(FAILED, ERROR)) == (6, 4)


def test_a_real_vitest_report_is_parsed() -> None:
    results = sample("vitest.xml")
    assert outcomes(results) == [
        ("math.test.js::math > adds", PASSED),
        ("math.test.js::math > fails", FAILED),
        ("math.test.js::math > is skipped", SKIPPED),
        ("math.test.js::math > throws", FAILED),
        ("other.test.js::top level", PASSED),
        ("other.test.js::later", SKIPPED),
    ]


def test_a_real_phpunit_report_with_nested_suites_is_parsed() -> None:
    # Real output of PHPUnit 13.4.3; it reports an incomplete test as skipped.
    results = sample("phpunit.xml")
    group = "App.Tests.MathTest"
    assert outcomes(results) == [
        (f"{group}::testAdds", PASSED),
        (f"{group}::testFails", FAILED),
        (f"{group}::testErrors", ERROR),
        (f"{group}::testSkipped", SKIPPED),
        (f"{group}::testIncomplete", SKIPPED),
        (f"{group}::testData with data set #0", PASSED),
        (f'{group}::testData with data set "two"', PASSED),
    ]
    assert (results.executed, results.count(FAILED, ERROR)) == (5, 2)


def test_a_pytest_expected_failure_is_neither_a_pass_nor_a_skip() -> None:
    xfail = '<testcase classname="c" name="x"><skipped type="pytest.xfail" message="m"/></testcase>'
    skip = '<testcase classname="c" name="s"><skipped type="pytest.skip" message="m"/></testcase>'
    data = f'<testsuite tests="2" skipped="2">{xfail}{skip}</testsuite>'.encode()
    assert outcomes(parse_junit(data, "r")) == [
        ("c::x", Outcome.EXPECTED_FAILURE),
        ("c::s", SKIPPED),
    ]


def test_a_single_testsuite_root_is_accepted() -> None:
    data = b'<testsuite tests="1"><testcase classname="c" name="t"/></testsuite>'
    assert parse_junit(data, "r") == SuiteResults((CaseResult("c::t", PASSED),))


@pytest.mark.parametrize("data", [b"<testsuites/>", b'<testsuites tests="0"></testsuites>'])
def test_an_empty_report_has_no_cases(data: bytes) -> None:
    results = parse_junit(data, "r")
    assert results.cases == ()
    assert results.executed == 0


def test_a_case_without_a_classname_is_named_alone() -> None:
    data = b'<testsuite><testcase name="alone"/><testcase classname="" name="empty"/></testsuite>'
    assert [case.name for case in parse_junit(data, "r").cases] == ["alone", "empty"]


@pytest.mark.parametrize(
    ("markers", "expected"),
    [
        ("<skipped/><failure/><error/>", ERROR),
        ("<skipped/><failure/>", FAILED),
        ("<failure/><error/>", ERROR),
        ("<skipped/>", SKIPPED),
        ("<system-out>log</system-out>", PASSED),
    ],
)
def test_the_most_severe_marker_of_a_case_wins(markers: str, expected: Outcome) -> None:
    data = f'<testsuite><testcase name="t">{markers}</testcase></testsuite>'.encode()
    assert parse_junit(data, "r").cases[0].outcome is expected


def test_a_report_with_another_root_is_rejected() -> None:
    with pytest.raises(ReportError, match=r"^r: not a JUnit report \(<coverage>\)$"):
        parse_junit(b"<coverage/>", "r")


@pytest.mark.parametrize("case", ["<testcase/>", '<testcase name=""/>'])
def test_a_case_without_a_name_is_rejected(case: str) -> None:
    data = f"<testsuites><testsuite>{case}</testsuite></testsuites>".encode()
    with pytest.raises(ReportError, match=r"^r: <testcase> without a name$"):
        parse_junit(data, "r")


@pytest.mark.parametrize(
    ("attribute", "actual"),
    [("tests", 2), ("failures", 1), ("errors", 0), ("skipped", 0)],
)
def test_declared_counts_must_match_the_cases(attribute: str, actual: int) -> None:
    cases = '<testcase name="a"/><testcase name="b"><failure/></testcase>'
    data = f'<testsuite name="s" {attribute}="7">{cases}</testsuite>'.encode()
    expected = f"^r: 's' declares {attribute}='7', but the report has {actual}$"
    with pytest.raises(ReportError, match=expected):
        parse_junit(data, "r")


@pytest.mark.parametrize("value", ["", "one", "-1", "1.0", " 1"])
def test_declared_counts_must_be_plain_numbers(value: str) -> None:
    data = f'<testsuite tests="{value}"><testcase name="a"/></testsuite>'.encode()
    with pytest.raises(ReportError, match=r"^r: 'testsuite' declares tests="):
        parse_junit(data, "r")


def test_outer_suites_count_the_cases_of_nested_suites() -> None:
    inner = '<testsuite name="inner" tests="1"><testcase name="a"><skipped/></testcase></testsuite>'
    data = f'<testsuites tests="1" skipped="1">{inner}</testsuites>'.encode()
    assert parse_junit(data, "r").count(SKIPPED) == 1
    lying = f'<testsuites tests="1" skipped="0">{inner}</testsuites>'.encode()
    with pytest.raises(ReportError, match="'testsuites' declares skipped='0'"):
        parse_junit(lying, "r")


def test_cases_outside_a_suite_element_are_not_collected() -> None:
    data = b'<testsuites><other><testcase name="hidden"/></other></testsuites>'
    assert parse_junit(data, "r").cases == ()


def test_suites_may_nest_up_to_the_depth_limit() -> None:
    def nested(levels: int) -> bytes:
        body = '<testcase name="deep"/>'
        for _ in range(levels):
            body = f"<testsuite>{body}</testsuite>"
        return f"<testsuites>{body}</testsuites>".encode()

    assert len(parse_junit(nested(MAX_DEPTH), "r").cases) == 1
    with pytest.raises(ReportError, match=f"nested deeper than {MAX_DEPTH} levels"):
        parse_junit(nested(MAX_DEPTH + 1), "r")


@pytest.mark.parametrize(
    "data",
    [
        b"<testsuites><testsuite>",
        b"",
        b"\xff\xfe<testsuites/>",
        b'<!DOCTYPE t [<!ENTITY x "boom">]><testsuites name="&x;"/>',
        b'<!DOCTYPE t SYSTEM "http://example.invalid/t.dtd"><testsuites/>',
    ],
    ids=["truncated", "empty", "bad-encoding", "entity", "external-dtd"],
)
def test_malformed_or_unsafe_xml_is_rejected(data: bytes) -> None:
    with pytest.raises(ReportError, match=r"^malformed or unsafe XML in r: "):
        parse_junit(data, "r")
