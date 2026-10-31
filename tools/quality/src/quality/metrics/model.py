"""The structure-metric capability: per-function measurements and imports of one file.

Adapters produce these values from a real parser of their language; rules compare
them with the policy thresholds. The counting rules are in decision 0007.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True, slots=True)
class FunctionMetrics:
    """Measurements of one function or method; `name` is qualified by its scopes."""

    name: str
    line: int
    code_lines: int
    complexity: int
    nesting: int
    parameters: int


@dataclass(frozen=True, slots=True)
class ImportRef:
    """One import as written; `level` counts the leading dots of a relative import."""

    module: str
    line: int
    level: int = 0
    names: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FileMetrics:
    """The functions and imports of one source file."""

    path: str
    functions: tuple[FunctionMetrics, ...]
    imports: tuple[ImportRef, ...]


class StructureError(Exception):
    """A source file cannot be parsed, so it cannot be measured."""

    def __init__(self, path: str, line: int | None, message: str) -> None:
        super().__init__(message)
        self.path = path
        self.line = line
        self.message = message


@dataclass(frozen=True, slots=True)
class Measurements:
    """What a profile adapter measured in one component with its own parser.

    `files` are the measured files and `errors` the files that did not parse.
    `dependencies` maps a measured file to the files it imports, each with the
    line of the first such import; the engine keeps the component's source files
    and checks the declared architecture with them.
    """

    files: tuple[FileMetrics, ...]
    errors: tuple[StructureError, ...] = ()
    dependencies: Mapping[str, Mapping[str, int]] = field(
        default_factory=dict[str, Mapping[str, int]]
    )


class StructureAnalyzer(Protocol):
    """Measure one file of a language; raise `StructureError` when it does not parse."""

    def analyze(self, path: str, source: bytes) -> FileMetrics:
        """Return the metrics of `source`, which is stored at repository path `path`."""
        ...
