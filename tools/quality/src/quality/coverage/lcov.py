"""LCOV tracefiles, as written by coverage.py, Vitest, and the Dart `coverage` package."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from quality.boundedio import ReportError
from quality.coverage.model import Counter, CoverageReport, FileCoverage
from quality.coverage.paths import repository_path

IGNORED = frozenset({"TN", "VER", "FN", "FNDA", "FNF", "FNH", "FNL", "FNA"})
DECLARED = frozenset({"LF", "LH", "BRF", "BRH"})
NUMBER = re.compile(r"[0-9]+")

type BranchKey = tuple[int, str, str]


def _number(text: str, where: str) -> int:
    if NUMBER.fullmatch(text) is None:
        msg = f"{where}: {text!r} is not a non-negative integer"
        raise ReportError(msg)
    return int(text)


def _line_number(text: str, where: str) -> int:
    number = _number(text, where)
    if number == 0:
        msg = f"{where}: line numbers start at 1"
        raise ReportError(msg)
    return number


def _branch_parts(value: str, where: str) -> tuple[str, str, str, str]:
    """Split `line,block,branch,taken`; the branch may be an expression with commas."""
    head = value.split(",", 2)
    if len(head) != 3 or "," not in head[2]:
        msg = f"{where}: BRDA needs a line, a block, a branch, and a taken count"
        raise ReportError(msg)
    branch, taken = head[2].rsplit(",", 1)
    return head[0], head[1], branch, taken


@dataclass(slots=True)
class _Record:
    """One `SF` ... `end_of_record` block; repeated blocks of a file are merged."""

    path: str
    hits: dict[int, int] = field(default_factory=dict[int, int])
    branches: dict[BranchKey, int] = field(default_factory=dict[BranchKey, int])
    declared: dict[str, int] = field(default_factory=dict[str, int])

    def check(self, where: str) -> None:
        actual = {
            "LF": len(self.hits),
            "LH": sum(1 for hits in self.hits.values() if hits),
            "BRF": len(self.branches),
            "BRH": sum(1 for taken in self.branches.values() if taken),
        }
        for key, value in self.declared.items():
            if actual[key] != value:
                msg = f"{where}: {key}:{value} disagrees with the records ({actual[key]})"
                raise ReportError(msg)

    def merge(self, other: _Record) -> None:
        for line, hits in other.hits.items():
            self.hits[line] = self.hits.get(line, 0) + hits
        for key, taken in other.branches.items():
            self.branches[key] = self.branches.get(key, 0) + taken

    def coverage(self) -> FileCoverage:
        covered = frozenset(line for line, hits in self.hits.items() if hits)
        taken = sum(1 for value in self.branches.values() if value)
        return FileCoverage(
            self.path, frozenset(self.hits), covered, Counter(taken, len(self.branches))
        )


@dataclass(slots=True)
class _Parser:
    origin: str
    root: Path
    cwd: Path
    records: dict[str, _Record] = field(default_factory=dict[str, _Record])
    current: _Record | None = None

    def feed(self, number: int, line: str) -> None:
        where = f"{self.origin}:{number}"
        if line == "end_of_record":
            self._end(where)
            return
        key, separator, value = line.partition(":")
        if not separator:
            msg = f"{where}: not an LCOV record: {line!r}"
            raise ReportError(msg)
        if key == "SF":
            self._start(value, where)
        elif key not in IGNORED:
            self._data(self._open(where), key, value, where)

    def _data(self, record: _Record, key: str, value: str, where: str) -> None:
        parts = value.split(",")
        if key == "DA":
            if len(parts) not in {2, 3}:
                msg = f"{where}: DA needs a line, a hit count, and an optional checksum"
                raise ReportError(msg)
            number = _line_number(parts[0], where)
            record.hits[number] = record.hits.get(number, 0) + _number(parts[1], where)
        elif key == "BRDA":
            line, block, branch, taken = _branch_parts(value, where)
            branch_key = (_line_number(line, where), block, branch)
            hits = 0 if taken == "-" else _number(taken, where)
            record.branches[branch_key] = record.branches.get(branch_key, 0) + hits
        elif key in DECLARED:
            record.declared[key] = _number(value, where)
        else:
            msg = f"{where}: unknown LCOV record {key!r}"
            raise ReportError(msg)

    def _open(self, where: str) -> _Record:
        if self.current is None:
            msg = f"{where}: record outside an SF block"
            raise ReportError(msg)
        return self.current

    def _start(self, name: str, where: str) -> None:
        if self.current is not None:
            msg = f"{where}: SF before the previous end_of_record"
            raise ReportError(msg)
        self.current = _Record(repository_path(name, root=self.root, cwd=self.cwd))

    def _end(self, where: str) -> None:
        record = self._open(where)
        record.check(where)
        if record.path in self.records:
            self.records[record.path].merge(record)
        else:
            self.records[record.path] = record
        self.current = None


def parse_lcov(
    data: bytes, origin: str, *, root: Path, cwd: Path, branches_measured: bool
) -> CoverageReport:
    """Parse and cross-check a tracefile; the adapter states whether branches were measured.

    LCOV cannot express that branch collection was off, so the adapter that enabled
    it says so. Declared `LF`/`LH`/`BRF`/`BRH` totals must match the records.
    """
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        msg = f"{origin}: not UTF-8: {error}"
        raise ReportError(msg) from error
    parser = _Parser(origin, root, cwd)
    for number, raw in enumerate(text.split("\n"), start=1):
        if line := raw.strip():
            parser.feed(number, line)
    if parser.current is not None:
        msg = f"{origin}: truncated report; the last record has no end_of_record"
        raise ReportError(msg)
    files = {path: record.coverage() for path, record in parser.records.items()}
    return CoverageReport(MappingProxyType(files), branches_measured)
