"""Structured diagnostics shared by every quality rule."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from types import MappingProxyType


class Severity(StrEnum):
    """Diagnostic severity; only errors fail a gate."""

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class Kind(StrEnum):
    """What an error diagnostic means for the exit code."""

    VIOLATION = "violation"
    USAGE = "usage"
    ENVIRONMENT = "environment"


class ExitCode(IntEnum):
    """Deterministic process exit codes of the quality CLI."""

    OK = 0
    VIOLATIONS = 1
    USAGE = 2
    ENVIRONMENT = 3
    INTERNAL = 4


@dataclass(frozen=True, slots=True)
class Location:
    """A repository-relative position; line and column are 1-based."""

    path: str
    line: int | None = None
    column: int | None = None

    def render(self) -> str:
        """Return the conventional `path:line:column` form."""
        parts = [self.path]
        if self.line is not None:
            parts.append(str(self.line))
            if self.column is not None:
                parts.append(str(self.column))
        return ":".join(parts)


def _empty_data() -> Mapping[str, str]:
    return MappingProxyType({})


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """One finding produced by a rule."""

    rule: str
    severity: Severity
    message: str
    location: Location | None = None
    component: str | None = None
    hint: str | None = None
    kind: Kind = Kind.VIOLATION
    data: Mapping[str, str] = field(default_factory=_empty_data)

    def sort_key(self) -> tuple[str, int, str, str]:
        """Order diagnostics by path, line, rule, and message."""
        if self.location is None:
            return ("", 0, self.rule, self.message)
        return (self.location.path, self.location.line or 0, self.rule, self.message)


@dataclass(frozen=True, slots=True)
class Outcome:
    """The ordered diagnostics of a run."""

    diagnostics: tuple[Diagnostic, ...]

    @classmethod
    def of(cls, diagnostics: Iterable[Diagnostic]) -> Outcome:
        """Build an outcome with deterministically ordered diagnostics."""
        return cls(tuple(sorted(diagnostics, key=Diagnostic.sort_key)))

    @property
    def errors(self) -> tuple[Diagnostic, ...]:
        """Diagnostics with error severity."""
        return tuple(d for d in self.diagnostics if d.severity is Severity.ERROR)

    @property
    def warnings(self) -> tuple[Diagnostic, ...]:
        """Diagnostics with warning severity."""
        return tuple(d for d in self.diagnostics if d.severity is Severity.WARNING)

    def exit_code(self) -> ExitCode:
        """Map the outcome to the CLI exit code; usage beats environment beats violations."""
        kinds = {d.kind for d in self.errors}
        if Kind.USAGE in kinds:
            return ExitCode.USAGE
        if Kind.ENVIRONMENT in kinds:
            return ExitCode.ENVIRONMENT
        if kinds:
            return ExitCode.VIOLATIONS
        return ExitCode.OK
