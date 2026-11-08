"""Normalized inputs shared by the common rules."""

from __future__ import annotations

from dataclasses import dataclass

from quality.discovery import FileRecord
from quality.policy import Category

TEXT_CATEGORIES = frozenset({Category.SOURCE, Category.TESTS, Category.DOCS, Category.CONFIG})


@dataclass(frozen=True, slots=True)
class CandidateFile:
    """A classified candidate file with its size and, for regular files, its bytes."""

    record: FileRecord
    size: int
    data: bytes | None = None

    @property
    def path(self) -> str:
        """The repository-relative path."""
        return self.record.path

    @property
    def category(self) -> Category:
        """The file category."""
        return self.record.category

    def text(self) -> str | None:
        """Decode the content as UTF-8; None for links and undecodable bytes."""
        if self.data is None:
            return None
        try:
            return self.data.decode("utf-8")
        except UnicodeDecodeError:
            return None


def physical_lines(data: bytes) -> int:
    """Count lines as editors do: a last line without a newline still counts."""
    return data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)
