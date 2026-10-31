"""The request and response files of the core's helper programs (decisions 0011 to 0013).

The TypeScript, Dart, and PHP helpers speak the same protocol: a request is a
JSON file in the work area, and the response is one JSON object on stdout that
carries the protocol version. The response is data the core checks field by
field; a missing field or one of an unexpected type is a failure of the named
helper, never a finding. A helper that cannot answer may say why in a string
`failure` field instead, which is a failure of the helper too.
"""

from __future__ import annotations

import json
import posixpath
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import cast

from quality.adapters.native import fail
from quality.boundedio import JsonValue, ReportError, parse_json
from quality.process import Completed

PROTOCOL = 1

type Entry = Mapping[str, JsonValue]


def write_request(directory: Path, command: str, request: Mapping[str, JsonValue]) -> Path:
    """Write one request with the protocol version into a new file in `directory`."""
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=directory, prefix=f"{command}-", suffix=".json", delete=False
    ) as handle:
        json.dump({"protocol": PROTOCOL, **request}, handle)
    return Path(handle.name)


def read_response(tool: str, command: str, completed: Completed) -> Entry:
    """Parse a helper's answer and require the protocol version."""
    try:
        response = parse_json(completed.stdout, f"{tool} {command} output")
    except ReportError as error:
        fail(tool, str(error))
    if not isinstance(response, dict) or response.get("protocol") != PROTOCOL:
        fail(tool, f"{command} answered without protocol {PROTOCOL}")
    reason = response.get("failure")
    if isinstance(reason, str):
        fail(tool, f"{command} failed: {reason}")
    return response


def repository_path(snapshot: Path, name: str) -> str | None:
    """Return the repository path of an absolute file name, or None outside the snapshot."""
    path = PurePosixPath(posixpath.normpath(name))
    for root in (snapshot, snapshot.resolve()):
        base = PurePosixPath(root.as_posix())
        if path.is_absolute() and path.is_relative_to(base) and path != base:
            return path.relative_to(base).as_posix()
    return None


@dataclass(frozen=True, slots=True)
class Fields:
    """Typed accessors for the fields of one helper's responses."""

    tool: str

    def entries(self, value: Entry, key: str) -> list[Entry]:
        """Return a list of objects, or fail."""
        found = value.get(key)
        if not isinstance(found, list) or not all(isinstance(item, dict) for item in found):
            fail(self.tool, f"'{key}' is not a list of objects")
        return cast(list[Entry], found)

    def text(self, value: Entry, key: str) -> str:
        """Return a string field, or fail."""
        found = value.get(key)
        if not isinstance(found, str):
            fail(self.tool, f"'{key}' is not a string")
        return found

    def texts(self, value: Entry, key: str) -> list[str]:
        """Return a list of strings, or fail."""
        found = value.get(key)
        if not isinstance(found, list) or not all(isinstance(item, str) for item in found):
            fail(self.tool, f"'{key}' is not a list of strings")
        return cast(list[str], found)

    def integer(self, value: Entry, key: str) -> int:
        """Return a non-negative integer field, or fail."""
        found = value.get(key)
        if not isinstance(found, int) or isinstance(found, bool) or found < 0:
            fail(self.tool, f"'{key}' is not a non-negative integer")
        return found

    def optional_integer(self, value: Entry, key: str) -> int | None:
        """Return a positive integer field or None, or fail."""
        if value.get(key) is None:
            return None
        return self.integer(value, key) or None

    def optional_text(self, value: Entry, key: str) -> str | None:
        """Return a string field or None, or fail."""
        return None if value.get(key) is None else self.text(value, key)

    def flag(self, value: Entry, key: str) -> bool:
        """Return a boolean field, or fail."""
        found = value.get(key)
        if not isinstance(found, bool):
            fail(self.tool, f"'{key}' is not a boolean")
        return found

    def optional_entry(self, value: Entry, key: str) -> Entry | None:
        """Return an object field or None, or fail."""
        found = value.get(key)
        if found is not None and not isinstance(found, dict):
            fail(self.tool, f"'{key}' is not an object")
        return found
