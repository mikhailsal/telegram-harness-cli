"""PHPUnit's coverage: lines from its JSONL report, branches from Clover (decision 0005).

php-code-coverage writes all its reports from the same data, but none of them
holds both counters exactly. Clover has the executed and executable branches
of each file, but it writes a method's record instead of the statement record
of the line where the method starts, so the statement of a one-line method
has no record. The JSONL report has each file's uncovered lines and, for each
test, the lines it executed, which together are the executable lines, but it
counts branches only for functions with more than one. The profile therefore
asks PHPUnit for both reports and takes the lines from JSONL and the branches
from Clover.

The reports are cross-checked: JSONL's per-file totals must match its lines,
its meta.json totals, and Clover's statement totals, every file JSONL lists
must be in Clover, and every Clover statement record must be an executable
line in the same state. JSONL names files relative to the `sourceRoot` of
meta.json and leaves out files without executable lines, which Clover keeps;
`branchCoverage` says whether the driver collected branches at all. The format
is that of the pinned php-code-coverage, schema version 1.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from quality.boundedio import JsonValue, ReportError, parse_json
from quality.coverage.clover import CloverFile, clover_files
from quality.coverage.model import Counter, CoverageReport, FileCoverage
from quality.coverage.paths import repository_path

SCHEMA = 1
RANGE = re.compile(r"([1-9][0-9]*)(?:-([1-9][0-9]*))?")
type JsonObject = dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class JsonlReport:
    """The three files of a JSONL report directory."""

    meta: bytes
    coverage: bytes
    tests: bytes


@dataclass(slots=True)
class _Lines:
    """One file of coverage.jsonl, with the covered lines that tests.jsonl adds."""

    executable: int
    executed: int
    uncovered: frozenset[int]
    covered: set[int]


@dataclass(frozen=True, slots=True)
class _Meta:
    root: str
    branches: bool
    files: int
    lines: Counter


def _fail(message: str) -> ReportError:
    return ReportError(message)


def _count(record: JsonObject, key: str, where: str) -> int:
    value = record.get(key)
    if type(value) is not int or value < 0:
        msg = f"{where}: '{key}' must be a non-negative integer"
        raise _fail(msg)
    return value


def _records(data: bytes, origin: str) -> Iterator[tuple[JsonObject, str]]:
    for number, line in enumerate(data.splitlines(), 1):
        where = f"{origin}:{number}"
        if not line.strip():
            continue
        value = parse_json(line, where)
        if not isinstance(value, dict):
            msg = f"{where}: a record must be a JSON object"
            raise _fail(msg)
        yield value, where


def _range(item: JsonValue, where: str) -> range:
    """Return the lines of one range such as `"7"` or `"9-12"`; a reversed range has none."""
    match = RANGE.fullmatch(item) if isinstance(item, str) else None
    lines = range(int(match[1]), int(match[2] or match[1]) + 1) if match else range(0)
    if not lines:
        msg = f"{where}: {item!r} is not a line range"
        raise _fail(msg)
    return lines


def _lines(value: JsonValue, limit: int, where: str) -> frozenset[int]:
    """Return the lines of a list of ranges, at most `limit`."""
    if not isinstance(value, list):
        msg = f"{where}: line ranges must be a list"
        raise _fail(msg)
    ranges = [_range(item, where) for item in value]
    if sum(len(lines) for lines in ranges) > limit:
        msg = f"{where}: more lines than the file's {limit} executable lines"
        raise _fail(msg)
    return frozenset(line for lines in ranges for line in lines)


def _meta(data: bytes) -> _Meta:
    meta = parse_json(data, "meta.json")
    version = meta.get("schemaVersion") if isinstance(meta, dict) else None
    if not isinstance(meta, dict) or type(version) is not int or version != SCHEMA:
        msg = f"meta.json: not a JSONL coverage report of schema version {SCHEMA}"
        raise _fail(msg)
    root, branches = meta.get("sourceRoot"), meta.get("branchCoverage")
    if not isinstance(root, str) or not root or not isinstance(branches, bool):
        msg = "meta.json: 'sourceRoot' and 'branchCoverage' are required"
        raise _fail(msg)
    executed, executable = (
        _count(meta, key, "meta.json") for key in ("executedLines", "executableLines")
    )
    if executed > executable:
        msg = "meta.json: more executed than executable lines"
        raise _fail(msg)
    return _Meta(root, branches, _count(meta, "files", "meta.json"), Counter(executed, executable))


def _files(data: bytes, meta: _Meta, *, root: Path, cwd: Path) -> dict[str, _Lines]:
    files: dict[str, _Lines] = {}
    for record, where in _records(data, "coverage.jsonl"):
        name = record.get("file")
        if not isinstance(name, str) or not name:
            msg = f"{where}: a record needs a 'file'"
            raise _fail(msg)
        path = repository_path(posixpath.join(meta.root, name), root=root, cwd=cwd)
        executable, executed = (
            _count(record, "executable", where),
            _count(record, "executed", where),
        )
        if path in files or executed > executable:
            msg = f"{where}: {path} is reported twice or with more executed than executable lines"
            raise _fail(msg)
        uncovered = _lines(record.get("uncovered", []), executable, where)
        files[path] = _Lines(executable, executed, uncovered, set())
    return files


def _cover(data: bytes, files: dict[str, _Lines], meta: _Meta, *, root: Path, cwd: Path) -> None:
    for record, where in _records(data, "tests.jsonl"):
        covers = record.get("covers")
        if not isinstance(record.get("test"), str) or not isinstance(covers, dict):
            msg = f"{where}: a record needs a 'test' and its 'covers'"
            raise _fail(msg)
        for name, ranges in covers.items():
            path = repository_path(posixpath.join(meta.root, name), root=root, cwd=cwd)
            file = files.get(path)
            if file is None:
                msg = f"{where}: {path} has no record in coverage.jsonl"
                raise _fail(msg)
            file.covered.update(_lines(ranges, file.executable, where))


def _checked(path: str, file: _Lines) -> tuple[frozenset[int], frozenset[int]]:
    covered = frozenset(file.covered)
    if covered & file.uncovered or len(covered) != file.executed:
        msg = f"tests.jsonl [{path}]: the covered lines disagree with coverage.jsonl"
        raise _fail(msg)
    if len(covered) + len(file.uncovered) != file.executable:
        msg = f"coverage.jsonl [{path}]: the executable lines disagree with the uncovered ones"
        raise _fail(msg)
    return covered | file.uncovered, covered


def _merged(
    path: str, clover: CloverFile, executable: frozenset[int], covered: frozenset[int]
) -> FileCoverage:
    if clover.lines != Counter(len(covered), len(executable)):
        msg = f"clover.xml [{path}]: the statement totals disagree with the JSONL report"
        raise _fail(msg)
    for line, count in clover.statements.items():
        if line not in executable or bool(count) != (line in covered):
            msg = f"clover.xml [{path}]: statement line {line} disagrees with the JSONL report"
            raise _fail(msg)
    return FileCoverage(path, executable, covered, clover.branches)


def parse_phpunit(clover: bytes, jsonl: JsonlReport, *, root: Path, cwd: Path) -> CoverageReport:
    """Combine PHPUnit's Clover and JSONL reports of one run, cross-checked."""
    meta = _meta(jsonl.meta)
    files = _files(jsonl.coverage, meta, root=root, cwd=cwd)
    _cover(jsonl.tests, files, meta, root=root, cwd=cwd)
    lines = {path: _checked(path, file) for path, file in files.items()}
    executed = sum(len(covered) for _, covered in lines.values())
    executable = sum(len(found) for found, _ in lines.values())
    if (meta.files, meta.lines) != (len(lines), Counter(executed, executable)):
        msg = "meta.json: the totals disagree with coverage.jsonl"
        raise _fail(msg)
    statements = clover_files(clover, "clover.xml", root=root, cwd=cwd)
    missing = sorted(set(lines) - set(statements))
    if missing:
        msg = f"clover.xml: {', '.join(missing)} are missing"
        raise _fail(msg)
    none: tuple[frozenset[int], frozenset[int]] = (frozenset(), frozenset())
    merged = {
        path: _merged(path, file, *lines.get(path, none)) for path, file in statements.items()
    }
    # Only functions have branches, and a function has executable lines.
    return CoverageReport(MappingProxyType(merged), meta.branches or not lines)
