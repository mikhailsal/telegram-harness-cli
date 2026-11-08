"""Vulnerability matching of lockfiles with the pinned osv-scanner.

Without a database directory the scanner asks the osv.dev API, so the advisory
data is current; with one it matches offline against the OSV database archives
there and never uses the network. Each lockfile is scanned from a copy under its
canonical name in a directory of its own, so a candidate path cannot be read as
a scanner option, and one run covers every lockfile.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from quality.adapters.native import fail, run_tool
from quality.boundedio import JsonValue, ReportError, parse_json
from quality.process import Command, sanitized

FOUND = 1
NO_PACKAGES = 128
TIMEOUT = 900.0
DATABASE = "OSV_SCANNER_LOCAL_DB_CACHE_DIRECTORY"


@dataclass(frozen=True, slots=True)
class Advisory:
    """One advisory group that affects one locked package; `score` is the highest CVSS score."""

    package: str
    version: str
    ecosystem: str
    ids: tuple[str, ...]
    aliases: tuple[str, ...]
    score: float | None


def _object(value: JsonValue, what: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        fail("osv-scanner", f"{what} is not an object")
    return value


def _list(value: JsonValue, what: str) -> list[JsonValue]:
    if not isinstance(value, list):
        fail("osv-scanner", f"{what} is not a list")
    return value


def _text(entry: dict[str, JsonValue], key: str) -> str:
    value = entry.get(key)
    if not isinstance(value, str):
        fail("osv-scanner", f"report entry without text field {key}")
    return value


def _texts(entry: dict[str, JsonValue], key: str) -> tuple[str, ...]:
    values = _list(entry.get(key), key)
    if not all(isinstance(value, str) for value in values):
        fail("osv-scanner", f"report field {key} holds a non-text entry")
    return tuple(sorted(str(value) for value in values))


def score(text: str) -> float | None:
    """Return a CVSS base score from 0 to 10, or None when the advisory has none."""
    try:
        value = float(text)
    except ValueError:
        return None
    return value if 0.0 <= value <= 10.0 else None


def _advisories(result: JsonValue) -> Iterator[Advisory]:
    for entry in _list(_object(result, "result").get("packages"), "packages"):
        found = _object(entry, "package entry")
        package = _object(found.get("package"), "package")
        for item in _list(found.get("groups"), "groups"):
            group = _object(item, "group")
            ids = _texts(group, "ids")
            if not ids:
                fail("osv-scanner", "advisory group without IDs")
            yield Advisory(
                package=_text(package, "name"),
                version=_text(package, "version"),
                ecosystem=_text(package, "ecosystem"),
                ids=ids,
                aliases=_texts(group, "aliases"),
                score=score(_text(group, "max_severity")),
            )


def _by_lockfile(report: JsonValue, copies: Mapping[str, int]) -> dict[int, list[Advisory]]:
    found: dict[int, list[Advisory]] = {}
    for result in _list(_object(report, "report").get("results"), "results"):
        source = _object(_object(result, "result").get("source"), "source")
        index = copies.get(_text(source, "path"))
        if index is None:
            fail("osv-scanner", f"result for an unknown lockfile: {_text(source, 'path')}")
        found.setdefault(index, []).extend(_advisories(result))
    return found


def scan_lockfiles(
    lockfiles: Sequence[Path], work: Path, *, scanner: Path, database: Path | None
) -> dict[int, list[Advisory]]:
    """Return the advisories that affect each lockfile's packages, by the lockfile's index."""
    if not lockfiles:
        return {}
    copies: dict[str, int] = {}
    argv = (str(scanner), "scan", "source", "--format", "json")
    if database is not None:
        argv += ("--offline", "--offline-vulnerabilities")
    for index, lockfile in enumerate(lockfiles):
        directory = work / str(index)
        directory.mkdir(parents=True)
        copy = directory / lockfile.name
        shutil.copyfile(lockfile, copy)
        copies[str(copy)] = index
        argv += ("--lockfile", str(copy))
    env = sanitized(extra={DATABASE: str(database)} if database is not None else None)
    command = Command(argv, cwd=work, timeout=TIMEOUT, env=env)
    completed = run_tool(command, name="osv-scanner", accepted={0, FOUND, NO_PACKAGES})
    if completed.returncode == NO_PACKAGES:
        return {}
    if completed.stdout_truncated:
        fail("osv-scanner", "the report exceeded the output limit")
    try:
        found = _by_lockfile(parse_json(completed.stdout, "osv-scanner report"), copies)
    except ReportError as error:
        fail("osv-scanner", str(error))
    if (completed.returncode == FOUND) != any(found.values()):
        fail("osv-scanner", "exit status and report disagree")
    return found
