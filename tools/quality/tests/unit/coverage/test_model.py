import pytest

from quality.coverage import EMPTY, Counter, FileCoverage, total


@pytest.mark.parametrize(("covered", "of"), [(-1, 0), (2, 1), (0, -1), (1, 0)])
def test_counters_reject_impossible_values(covered: int, of: int) -> None:
    with pytest.raises(ValueError, match=rf"^invalid counter {covered}/{of}$"):
        Counter(covered, of)


def test_counters_add_numerators_and_denominators_separately() -> None:
    assert Counter(1, 2) + Counter(3, 4) == Counter(4, 6)
    assert total([Counter(1, 3), Counter(0, 2), EMPTY]) == Counter(1, 5)
    assert total([]) == EMPTY


def test_only_a_positive_total_is_applicable() -> None:
    assert not EMPTY.applicable
    assert Counter(0, 1).applicable


@pytest.mark.parametrize(
    ("counter", "minimum", "expected"),
    [
        (Counter(80, 100), 80, True),
        (Counter(79, 100), 80, False),
        (Counter(799, 1000), 80, False),
        (Counter(4, 5), 80, True),
        (Counter(3, 4), 80, False),
        (Counter(7, 10), 70, True),
        (Counter(69, 100), 70, False),
        (Counter(9, 10), 90, True),
        (Counter(8, 9), 90, False),
        (Counter(1, 3), 33, True),
        (Counter(1, 3), 34, False),
        (Counter(2, 3), 66, True),
        (Counter(2, 3), 67, False),
        (Counter(1, 2), 50, True),
        (Counter(1, 2), 51, False),
        (Counter(0, 1), 0, True),
        (Counter(0, 1), 1, False),
        (Counter(1, 1), 100, True),
        (Counter(99_999, 100_000), 100, False),
    ],
)
def test_minima_are_compared_exactly(counter: Counter, minimum: int, expected: bool) -> None:
    assert counter.meets(minimum) is expected


@pytest.mark.parametrize(
    ("counter", "text"),
    [
        (Counter(2, 3), "66.66% (2/3)"),
        (Counter(1, 3), "33.33% (1/3)"),
        (Counter(1, 7), "14.28% (1/7)"),
        (Counter(199, 200), "99.50% (199/200)"),
        (Counter(99_999, 100_000), "99.99% (99999/100000)"),
        (Counter(1, 1), "100.00% (1/1)"),
        (Counter(0, 4), "0.00% (0/4)"),
        (EMPTY, "not applicable (0/0)"),
    ],
)
def test_descriptions_truncate_and_never_round_up(counter: Counter, text: str) -> None:
    assert counter.describe() == text


def test_file_coverage_counts_its_lines() -> None:
    file = FileCoverage("a.py", frozenset({1, 2, 5}), frozenset({2}), Counter(1, 4))
    assert file.lines == Counter(1, 3)
    assert FileCoverage("b.py", frozenset(), frozenset()).branches == EMPTY


def test_covered_lines_must_be_executable() -> None:
    with pytest.raises(ValueError, match=r"^a\.py: covered lines must be executable lines$"):
        FileCoverage("a.py", frozenset({1}), frozenset({1, 2}))


def test_line_numbers_start_at_one() -> None:
    with pytest.raises(ValueError, match=r"^a\.py: line numbers start at 1$"):
        FileCoverage("a.py", frozenset({0, 1}), frozenset({1}))
