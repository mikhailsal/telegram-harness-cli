"""Maintainer-only regions of the files a template export rewrites (decision 0016)."""

from __future__ import annotations

from dataclasses import dataclass, field

BEGIN = "maintainer-only:begin"
END = "maintainer-only:end"


class FenceError(ValueError):
    """A fence that does not open and close on its own lines."""

    def __init__(self, line: int, message: str) -> None:
        super().__init__(message)
        self.line = line


def _blank(line: str) -> bool:
    return not line.strip()


@dataclass(slots=True)
class _Stripper:
    kept: list[str] = field(default_factory=list[str])
    opened: int | None = None
    fences: int = 0
    after: bool = False

    def line(self, number: int, line: str) -> None:
        if BEGIN in line:
            self._open(number)
        elif END in line:
            self._close(number)
        elif self.opened is None:
            self._keep(line)

    def _open(self, number: int) -> None:
        if self.opened is not None:
            msg = f"opens a fence inside the one opened on line {self.opened}"
            raise FenceError(number, msg)
        self.opened = number
        self.fences += 1

    def _close(self, number: int) -> None:
        if self.opened is None:
            raise FenceError(number, "closes a fence that is not open")
        self.opened = None
        self.after = True

    def _keep(self, line: str) -> None:
        doubled = self.after and _blank(line) and (not self.kept or _blank(self.kept[-1]))
        if not doubled:
            self.kept.append(line)
        self.after = False

    def result(self) -> str:
        if self.opened is not None:
            raise FenceError(self.opened, "opens a fence that is never closed")
        if not self.fences:
            raise FenceError(1, f"has no '{BEGIN}' fence")
        while self.after and self.kept and _blank(self.kept[-1]):
            self.kept.pop()
        return "".join(self.kept)


def strip_fences(text: str) -> str:
    """Return `text` without its fenced regions, including the fence lines.

    A blank line that would double the one before a removed region is dropped,
    and so are blank lines a removed region would leave at the end, so Markdown
    and TOML stay tidy. A file without any fence is an error, because only
    files that need rewriting are declared.
    """
    stripper = _Stripper()
    for number, line in enumerate(text.splitlines(keepends=True), start=1):
        stripper.line(number, line)
    return stripper.result()
