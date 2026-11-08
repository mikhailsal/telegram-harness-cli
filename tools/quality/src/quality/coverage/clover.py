"""Clover XML reports, as written by PHPUnit with Xdebug branch coverage.

Each `<file>` has `<line type="stmt">` records with execution counts and
`<metrics>` with its statement and branch (`conditionals`) totals. PHPUnit
writes a method's record instead of a statement record when both fall on the
same line, so the statement of a one-line method is counted in the totals but
has no record of its own. The lines therefore come from PHPUnit's JSONL report
and only the branches from Clover (`phpunit.py`); the records that Clover has
are checked against JSONL there.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from xml.etree.ElementTree import Element

from quality.boundedio import ReportError, parse_xml
from quality.coverage.model import Counter
from quality.coverage.paths import repository_path

NUMBER = re.compile(r"[0-9]+")


@dataclass(frozen=True, slots=True)
class CloverFile:
    """One `<file>`: its statement records by line with their counts, and its totals."""

    path: str
    statements: Mapping[int, int]
    lines: Counter
    branches: Counter


def _attribute(element: Element, name: str, where: str) -> int:
    value = element.get(name)
    if value is None or NUMBER.fullmatch(value) is None:
        msg = f"{where}: <{element.tag}> needs a non-negative integer '{name}'"
        raise ReportError(msg)
    return int(value)


def _statements(element: Element, where: str) -> dict[int, int]:
    statements: dict[int, int] = {}
    for line in element.findall("line"):
        if line.get("type") != "stmt":
            continue
        number = _attribute(line, "num", where)
        if number == 0 or number in statements:
            msg = f"{where}: statement line {number} is invalid or repeated"
            raise ReportError(msg)
        statements[number] = _attribute(line, "count", where)
    return statements


def _counter(metrics: Element, covered: str, total: str, what: str, where: str) -> Counter:
    taken, possible = _attribute(metrics, covered, where), _attribute(metrics, total, where)
    if taken > possible:
        msg = f"{where}: more covered {what} than {what}"
        raise ReportError(msg)
    return Counter(taken, possible)


def _file(path: str, element: Element, where: str) -> CloverFile:
    metrics = element.find("metrics")
    if metrics is None:
        msg = f"{where}: <file> has no <metrics>"
        raise ReportError(msg)
    return CloverFile(
        path,
        MappingProxyType(_statements(element, where)),
        _counter(metrics, "coveredstatements", "statements", "statements", where),
        _counter(metrics, "coveredconditionals", "conditionals", "conditionals", where),
    )


def clover_files(data: bytes, origin: str, *, root: Path, cwd: Path) -> dict[str, CloverFile]:
    """Parse a Clover report into its files, keyed by repository path."""
    document = parse_xml(data, origin)
    project = document.find("project") if document.tag == "coverage" else None
    if project is None:
        msg = f"{origin}: not a Clover report (<coverage><project>)"
        raise ReportError(msg)
    files: dict[str, CloverFile] = {}
    for element in project.iter("file"):
        name = element.get("name")
        if not name:
            msg = f"{origin}: <file> without a name"
            raise ReportError(msg)
        path = repository_path(name, root=root, cwd=cwd)
        if path in files:
            msg = f"{origin}: {path} is reported twice"
            raise ReportError(msg)
        files[path] = _file(path, element, f"{origin} [{path}]")
    return files
