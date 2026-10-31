"""coverage.py JSON reports (format 3), the native Python coverage format."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from types import MappingProxyType

from quality.boundedio import JsonValue, ReportError, parse_json
from quality.coverage.model import EMPTY, Counter, CoverageReport, FileCoverage, total
from quality.coverage.paths import repository_path

FORMAT = 3

type JsonObject = dict[str, JsonValue]
type Arc = tuple[int, int]


def _fail(origin: str, message: str) -> ReportError:
    return ReportError(f"{origin}: {message}")


def _object(value: JsonValue, origin: str, what: str) -> JsonObject:
    if not isinstance(value, dict):
        raise _fail(origin, f"{what} is not an object")
    return value


def _count(value: JsonValue, origin: str, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail(origin, f"{what} is not a non-negative integer")
    return value


def _lines(value: JsonValue, origin: str, what: str) -> frozenset[int]:
    if not isinstance(value, list):
        raise _fail(origin, f"{what} is not a list")
    lines = [_count(item, origin, what) for item in value]
    if 0 in lines or len(set(lines)) != len(lines):
        raise _fail(origin, f"{what} has invalid or repeated line numbers")
    return frozenset(lines)


def _arc(item: JsonValue, origin: str, what: str) -> Arc:
    match item:
        case [int() as start, int() as end] if bool not in {type(start), type(end)}:
            return start, end
        case _:
            raise _fail(origin, f"{what} has an entry that is not a pair of line numbers")


def _arcs(value: JsonValue, origin: str, what: str) -> frozenset[Arc]:
    """Branch arcs: pairs of line numbers, with negative numbers for exits."""
    if not isinstance(value, list):
        raise _fail(origin, f"{what} is not a list")
    arcs = [_arc(item, origin, what) for item in value]
    if len(set(arcs)) != len(arcs):
        raise _fail(origin, f"{what} has repeated arcs")
    return frozenset(arcs)


def _agree(summary: JsonObject, key: str, expected: int, origin: str) -> None:
    if _count(summary.get(key), origin, key) != expected:
        raise _fail(origin, f"{key} disagrees with the listed lines or branches")


def _branches(entry: JsonObject, summary: JsonObject, origin: str) -> Counter:
    taken = _arcs(entry.get("executed_branches"), origin, "executed_branches")
    missed = _arcs(entry.get("missing_branches"), origin, "missing_branches")
    if taken & missed:
        raise _fail(origin, "an arc is both executed and missing")
    _agree(summary, "num_branches", len(taken | missed), origin)
    _agree(summary, "covered_branches", len(taken), origin)
    return Counter(len(taken), len(taken | missed))


def _file(path: str, entry: JsonObject, origin: str, *, measured: bool) -> FileCoverage:
    executed = _lines(entry.get("executed_lines"), origin, "executed_lines")
    missing = _lines(entry.get("missing_lines"), origin, "missing_lines")
    if executed & missing:
        raise _fail(origin, "a line is both executed and missing")
    summary = _object(entry.get("summary"), origin, "summary")
    _agree(summary, "num_statements", len(executed | missing), origin)
    _agree(summary, "covered_lines", len(executed), origin)
    branches = _branches(entry, summary, origin) if measured else EMPTY
    return FileCoverage(path, executed | missing, executed, branches)


def _check_totals(
    totals: JsonObject, files: Iterable[FileCoverage], origin: str, *, measured: bool
) -> None:
    counted = list(files)
    lines = total(file.lines for file in counted)
    _agree(totals, "num_statements", lines.total, origin)
    _agree(totals, "covered_lines", lines.covered, origin)
    if measured:
        branches = total(file.branches for file in counted)
        _agree(totals, "num_branches", branches.total, origin)
        _agree(totals, "covered_branches", branches.covered, origin)


def parse_coverage_json(data: bytes, origin: str, *, root: Path, cwd: Path) -> CoverageReport:
    """Parse and cross-check a report written by `coverage json` or `pytest --cov-report=json`."""
    document = _object(parse_json(data, origin), origin, "the report")
    meta = _object(document.get("meta"), origin, "meta")
    if meta.get("format") != FORMAT:
        raise _fail(origin, f"unsupported format {meta.get('format')!r}; expected {FORMAT}")
    measured = meta.get("branch_coverage") is True
    files: dict[str, FileCoverage] = {}
    for name, entry in _object(document.get("files"), origin, "files").items():
        path = repository_path(name, root=root, cwd=cwd)
        if path in files:
            raise _fail(origin, f"{path} is reported twice")
        where = f"{origin} [{path}]"
        files[path] = _file(path, _object(entry, origin, name), where, measured=measured)
    totals = _object(document.get("totals"), origin, "totals")
    _check_totals(totals, files.values(), origin, measured=measured)
    return CoverageReport(MappingProxyType(files), measured)
