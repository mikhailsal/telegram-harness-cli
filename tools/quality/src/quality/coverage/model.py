"""Normalized coverage: executable lines and native branch counts per source file.

Counters hold exact integers. Thresholds compare numerators and denominators;
percentages are only displayed, never compared.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Counter:
    """Covered and total units of one measurement; a total of zero is not applicable."""

    covered: int
    total: int

    def __post_init__(self) -> None:
        if not 0 <= self.covered <= self.total:
            msg = f"invalid counter {self.covered}/{self.total}"
            raise ValueError(msg)

    def __add__(self, other: Counter) -> Counter:
        return Counter(self.covered + other.covered, self.total + other.total)

    @property
    def applicable(self) -> bool:
        """Whether there is anything to measure."""
        return self.total > 0

    def meets(self, minimum: int) -> bool:
        """Return whether covered / total >= minimum / 100, compared exactly."""
        return self.covered * 100 >= minimum * self.total

    def describe(self) -> str:
        """Show the ratio and its percentage truncated to two decimals, never rounded up."""
        if not self.applicable:
            return "not applicable (0/0)"
        hundredths = self.covered * 10_000 // self.total
        return f"{hundredths // 100}.{hundredths % 100:02d}% ({self.covered}/{self.total})"


EMPTY = Counter(0, 0)


def total(counters: Iterable[Counter]) -> Counter:
    """Add counters of the same measurement."""
    return sum(counters, EMPTY)


@dataclass(frozen=True, slots=True)
class FileCoverage:
    """One source file: its executable lines, the covered subset, and its branch counts.

    `branches_measured` is false when the tool measured the file's lines but not
    its branches although it has some, such as a PHP file that Xdebug never
    loaded: its branch counter is then unknown, not zero.
    """

    path: str
    executable: frozenset[int]
    covered: frozenset[int]
    branches: Counter = EMPTY
    branches_measured: bool = True

    def __post_init__(self) -> None:
        if not self.covered <= self.executable:
            msg = f"{self.path}: covered lines must be executable lines"
            raise ValueError(msg)
        if any(line < 1 for line in self.executable):
            msg = f"{self.path}: line numbers start at 1"
            raise ValueError(msg)

    @property
    def lines(self) -> Counter:
        """The file's line counter."""
        return Counter(len(self.covered), len(self.executable))


@dataclass(frozen=True, slots=True)
class CoverageReport:
    """The normalized coverage of one run, keyed by repository path.

    `branches_measured` is false when the run did not collect branch data, which
    is an error rather than a branch total of zero.
    """

    files: Mapping[str, FileCoverage]
    branches_measured: bool
