"""The files of one component that a profile's static tools check, and their reports."""

from __future__ import annotations

import posixpath
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import cast

from quality.adapters.native import fail
from quality.boundedio import JsonValue, ReportError, parse_json
from quality.diagnostics import Location
from quality.policy import Component
from quality.process import Completed

type Entry = dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class Target:
    """Repository-relative files of one component inside a materialized snapshot."""

    component: Component
    snapshot: Path
    files: tuple[str, ...]

    @property
    def directory(self) -> Path:
        """The component directory the tools run in."""
        return self.snapshot / self.component.root

    def arguments(self) -> tuple[str, ...]:
        """Return the files as absolute paths, so no argument can be read as an option."""
        return tuple(str(self.snapshot / path) for path in self.files)

    def relative(self, tool: str, filename: JsonValue) -> str:
        """Map a file name from a tool report back to a repository path."""
        if not isinstance(filename, str) or not filename:
            fail(tool, "report entry without a file name")
        path = PurePosixPath(
            posixpath.normpath(posixpath.join(self.directory.as_posix(), filename))
        )
        roots = (
            PurePosixPath(root.as_posix()) for root in (self.snapshot, self.snapshot.resolve())
        )
        found = [path.relative_to(root).as_posix() for root in roots if path.is_relative_to(root)]
        if not found:
            fail(tool, f"report names a file outside the snapshot: {filename}")
        return found[0]


def document(tool: str, completed: Completed) -> JsonValue:
    """Parse a tool's JSON output; unusable output is a tool failure."""
    try:
        return parse_json(completed.stdout, f"{tool} output")
    except ReportError as error:
        fail(tool, str(error))


def findings(tool: str, value: JsonValue) -> list[Entry]:
    """Require a list of finding objects."""
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        fail(tool, "output is not a list of findings")
    return cast(list[Entry], value)


def number(entry: Entry, key: str) -> int | None:
    """Return a non-negative integer field, or None when it is absent or malformed."""
    value = entry.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def location(path: str, line: int | None, column: int | None) -> Location:
    """Build a location from 1-based positions; a missing line drops the column too."""
    return Location(path, line, column if line is not None else None)
