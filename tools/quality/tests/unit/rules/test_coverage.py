from collections.abc import Iterable, Mapping
from types import MappingProxyType

import pytest

from quality.coverage import EMPTY, Counter, CoverageReport, FileCoverage
from quality.diagnostics import Diagnostic, Kind, Location, Severity
from quality.policy import CoveragePolicy
from quality.rules.coverage import ComponentCoverage, Expectation, evaluate
from quality.testkit.policies import build_policy

MINIMA: CoveragePolicy = build_policy().coverage
SOURCE = "src/app/core.py"
OTHER = "src/app/util.py"


def measured(path: str, executable: int, covered: int, branches: Counter = EMPTY) -> FileCoverage:
    """Return a file whose first `covered` of `executable` lines ran."""
    return FileCoverage(
        path, frozenset(range(1, executable + 1)), frozenset(range(1, covered + 1)), branches
    )


def report(*files: FileCoverage, branches: bool = True) -> CoverageReport:
    return CoverageReport(MappingProxyType({file.path: file for file in files}), branches)


def expect(
    *sources: str,
    changed: Mapping[str, Iterable[int]] | None = None,
    added: Iterable[str] = (),
    missing_roots: tuple[str, ...] = (),
) -> Expectation:
    lines = {path: frozenset(numbers) for path, numbers in (changed or {}).items()}
    return Expectation("app", frozenset(sources), missing_roots, lines, frozenset(added))


def judged(
    expectation: Expectation, coverage: CoverageReport, minima: CoveragePolicy = MINIMA
) -> tuple[ComponentCoverage, list[Diagnostic]]:
    return evaluate(expectation, coverage, minima)


def rules_of(found: list[Diagnostic]) -> list[str]:
    return [diagnostic.rule for diagnostic in found]


def test_coverage_exactly_at_every_floor_passes() -> None:
    summary, found = judged(expect(SOURCE), report(measured(SOURCE, 100, 80, Counter(7, 10))))
    assert found == []
    assert summary == ComponentCoverage("app", Counter(80, 100), Counter(7, 10), EMPTY)


@pytest.mark.parametrize(
    ("lines", "branches", "rule", "message"),
    [
        ((100, 79), Counter(7, 10), "coverage.line", "line coverage of 'app' is 79.00% (79/100)"),
        ((5, 3), Counter(1, 1), "coverage.line", "line coverage of 'app' is 60.00% (3/5)"),
        ((1, 1), Counter(69, 100), "coverage.branch", "branch coverage of 'app' is 69.00%"),
        ((1, 1), Counter(2, 3), "coverage.branch", "branch coverage of 'app' is 66.66% (2/3)"),
        ((1, 0), Counter(1, 1), "coverage.line", "line coverage of 'app' is 0.00% (0/1)"),
    ],
)
def test_just_below_a_floor_fails(
    lines: tuple[int, int], branches: Counter, rule: str, message: str
) -> None:
    _, found = judged(expect(SOURCE), report(measured(SOURCE, *lines, branches)))
    assert rules_of(found) == [rule]
    assert found[0].message.startswith(message)
    assert found[0].message.endswith("below the minimum of 80%" if "line" in rule else "70%")
    assert found[0].component == "app"


def test_counters_are_added_across_files_never_averaged() -> None:
    # 1/2 and 9/10 average to 70%, but 10/12 lines are 83.33%.
    files = (measured(SOURCE, 2, 1, Counter(3, 4)), measured(OTHER, 10, 9, Counter(0, 1)))
    summary, found = judged(expect(SOURCE, OTHER), report(*files))
    assert (summary.line, summary.branch) == (Counter(10, 12), Counter(3, 5))
    assert rules_of(found) == ["coverage.branch"]


def test_a_file_the_report_omits_is_unaccounted() -> None:
    summary, found = judged(expect(SOURCE, OTHER), report(measured(SOURCE, 1, 1, Counter(1, 1))))
    assert rules_of(found) == ["coverage.unaccounted"]
    assert found[0].location == Location(OTHER)
    assert found[0].message == f"'{OTHER}' of 'app' is missing from the unit coverage report"
    assert summary.line == Counter(1, 1)


def test_files_other_than_the_expected_sources_are_ignored() -> None:
    files = (
        measured(SOURCE, 10, 1, Counter(0, 2)),
        measured("tests/test_core.py", 1000, 1000, Counter(100, 100)),
        measured("lib/other/mod.py", 1000, 1000, Counter(100, 100)),
    )
    summary, found = judged(expect(SOURCE), report(*files))
    assert summary.line == Counter(1, 10)
    assert rules_of(found) == ["coverage.line", "coverage.branch"]


def test_sources_without_executable_lines_are_not_applicable() -> None:
    summary, found = judged(expect(SOURCE), report(measured(SOURCE, 0, 0)))
    assert rules_of(found) == ["coverage.not-applicable", "coverage.not-applicable"]
    assert [d.severity for d in found] == [Severity.INFO, Severity.INFO]
    assert [d.message for d in found] == [
        "line coverage of 'app' is not applicable (0/0)",
        "branch coverage of 'app' is not applicable (0/0)",
    ]
    assert summary == ComponentCoverage("app", EMPTY, EMPTY, EMPTY)


def test_no_expected_sources_under_existing_roots_is_not_applicable() -> None:
    _, found = judged(expect(), report())
    assert rules_of(found) == ["coverage.not-applicable", "coverage.not-applicable"]


def test_a_broken_source_root_is_never_not_applicable() -> None:
    _, found = judged(expect(missing_roots=("src", "lib")), report())
    assert rules_of(found) == ["coverage.no-source"]
    assert found[0].message == (
        "'app' has no expected source files; missing source roots: 'src', 'lib'"
    )
    assert found[0].severity is Severity.ERROR


def test_a_missing_root_beside_real_sources_is_left_to_discovery() -> None:
    file = measured(SOURCE, 1, 1, Counter(1, 1))
    _, found = judged(expect(SOURCE, missing_roots=("lib",)), report(file))
    assert rules_of(found) == []


def test_a_report_without_branch_data_cannot_pass() -> None:
    summary, found = judged(expect(SOURCE), report(measured(SOURCE, 1, 1), branches=False))
    assert summary.branch is None
    assert rules_of(found) == ["coverage.branch-unmeasured"]
    assert found[0].kind is Kind.ENVIRONMENT


def test_a_source_file_whose_branches_were_not_measured_cannot_pass() -> None:
    loaded = measured(SOURCE, 4, 4, Counter(2, 2))
    never = FileCoverage(OTHER, frozenset({3}), frozenset(), branches_measured=False)
    ignored = FileCoverage("src/other.py", frozenset({3}), frozenset(), branches_measured=False)
    summary, found = judged(expect(SOURCE, OTHER), report(loaded, never, ignored))
    assert summary.branch == Counter(2, 2)
    assert rules_of(found) == ["coverage.branch-unmeasured"]
    assert found[0].message == (f"the coverage tool did not measure the branches of '{OTHER}'")
    assert (found[0].location, found[0].kind) == (Location(OTHER), Kind.ENVIRONMENT)


def test_measured_code_without_branches_is_not_applicable() -> None:
    _, found = judged(expect(SOURCE), report(measured(SOURCE, 4, 4)))
    assert [d.message for d in found] == ["branch coverage of 'app' is not applicable (0/0)"]


def test_uncovered_changes_fail_even_when_the_totals_pass() -> None:
    changed = {SOURCE: range(91, 101)}
    summary, found = judged(
        expect(SOURCE, changed=changed), report(measured(SOURCE, 100, 92, Counter(1, 1)))
    )
    assert summary.changed == Counter(2, 10)
    assert rules_of(found) == ["coverage.changed-lines"]
    assert found[0].message == (
        "changed-line coverage of 'app' is 20.00% (2/10), below the minimum of 90%; "
        f"uncovered: {', '.join(f'{SOURCE}:{n}' for n in range(93, 101))}"
    )
    assert dict(found[0].data) == {"covered": "2", "total": "10"}


@pytest.mark.parametrize(("covered", "failing"), [(9, False), (8, True)])
def test_changed_lines_floor_is_exact(*, covered: int, failing: bool) -> None:
    file = measured(SOURCE, 10, covered, Counter(1, 1))
    _, found = judged(expect(SOURCE, changed={SOURCE: range(1, 11)}), report(file))
    assert ("coverage.changed-lines" in rules_of(found)) is failing


@pytest.mark.parametrize(("covered", "failing"), [(0, True), (1, False)])
def test_a_one_line_change_is_measured(*, covered: int, failing: bool) -> None:
    file = measured(SOURCE, 1, covered, Counter(1, 1))
    _, found = judged(expect(SOURCE, changed={SOURCE: [1]}), report(file))
    assert ("coverage.changed-lines" in rules_of(found)) is failing


def test_changes_to_non_executable_lines_and_other_files_are_not_counted() -> None:
    changed = {SOURCE: [50, 51], "tests/test_core.py": [1], "README.md": [1]}
    summary, found = judged(
        expect(SOURCE, changed=changed), report(measured(SOURCE, 10, 10, Counter(1, 1)))
    )
    assert summary.changed == EMPTY
    assert found == []


def test_the_uncovered_change_list_is_bounded() -> None:
    file = measured(SOURCE, 30, 0, Counter(1, 1))
    _, found = judged(expect(SOURCE, changed={SOURCE: range(1, 31)}), report(file))
    changed = next(d for d in found if d.rule == "coverage.changed-lines")
    assert changed.message.endswith(f"{SOURCE}:20 and 10 more")


def test_a_new_file_without_coverage_fails() -> None:
    files = (measured(SOURCE, 100, 100, Counter(1, 1)), measured(OTHER, 2, 0))
    _, found = judged(expect(SOURCE, OTHER, added=[OTHER]), report(*files))
    assert rules_of(found) == ["coverage.new-file"]
    assert found[0].message == f"new file '{OTHER}' has no unit coverage (0.00% (0/2))"
    assert found[0].location == Location(OTHER)


@pytest.mark.parametrize(
    ("file", "added"),
    [
        (measured(OTHER, 2, 1), [OTHER]),
        (measured(OTHER, 0, 0), [OTHER]),
        (measured(OTHER, 2, 0), []),
    ],
)
def test_new_files_with_some_coverage_or_nothing_to_cover_pass(
    file: FileCoverage, added: list[str]
) -> None:
    files = (measured(SOURCE, 100, 100, Counter(1, 1)), file)
    _, found = judged(expect(SOURCE, OTHER, added=added), report(*files))
    assert "coverage.new-file" not in rules_of(found)


def test_the_new_file_rule_follows_the_policy() -> None:
    relaxed = MINIMA.model_copy(update={"new_file_must_be_covered": False})
    files = (measured(SOURCE, 100, 100, Counter(1, 1)), measured(OTHER, 2, 0))
    _, found = judged(expect(SOURCE, OTHER, added=[OTHER]), report(*files), relaxed)
    assert found == []
