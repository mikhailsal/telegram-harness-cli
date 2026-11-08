"""Bounded, non-executing readers for native tool reports.

Reports are data: JSON is parsed without object hooks, XML is parsed with
defusedxml (no DTDs, no entity expansion, no external resolution). Nothing is
deserialized into executable objects and report contents are never run.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast
from xml.etree.ElementTree import Element

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import fromstring

from quality import strictjson

DEFAULT_LIMIT = 256 * 1024 * 1024

type JsonValue = bool | int | float | str | list[JsonValue] | dict[str, JsonValue] | None


class ReportError(Exception):
    """A native report is missing, oversized, or malformed."""


def read_bounded(path: Path, limit: int = DEFAULT_LIMIT) -> bytes:
    """Read a regular file no larger than `limit` bytes."""
    if path.is_symlink() or not path.is_file():
        msg = f"report not found or not a regular file: {path}"
        raise ReportError(msg)
    size = path.stat().st_size
    if size > limit:
        msg = f"report exceeds {limit} bytes: {path} ({size} bytes)"
        raise ReportError(msg)
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        msg = f"report grew beyond {limit} bytes while reading: {path}"
        raise ReportError(msg)
    return data


def parse_json(data: bytes, origin: str) -> JsonValue:
    """Parse strict JSON (no NaN or Infinity, bounded nesting) from bytes."""
    try:
        return cast(JsonValue, strictjson.loads(data.decode("utf-8")))
    except (UnicodeDecodeError, ValueError) as exc:
        msg = f"malformed JSON in {origin}: {exc}"
        raise ReportError(msg) from exc


def load_json(path: Path, limit: int = DEFAULT_LIMIT) -> JsonValue:
    """Read and parse a bounded JSON report."""
    return parse_json(read_bounded(path, limit), str(path))


def parse_xml(data: bytes, origin: str) -> Element:
    """Parse XML with DTDs and entities forbidden."""
    try:
        return fromstring(data, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except (DefusedXmlException, SyntaxError, ValueError) as exc:
        msg = f"malformed or unsafe XML in {origin}: {exc}"
        raise ReportError(msg) from exc


def load_xml(path: Path, limit: int = DEFAULT_LIMIT) -> Element:
    """Read and parse a bounded XML report."""
    return parse_xml(read_bounded(path, limit), str(path))
