"""Internal link resolution against the candidate files, with exact, case-sensitive paths."""

from __future__ import annotations

import posixpath
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from functools import cached_property
from pathlib import PurePosixPath
from urllib.parse import unquote

from quality.rules.markdown import MarkdownFacts, parse_markdown
from quality.rules.model import CandidateFile

MARKDOWN_SUFFIXES = frozenset({".md", ".markdown"})
SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:|^//")
LINE_ANCHOR = re.compile(r"^L\d+(?:C\d+)?(?:-L\d+(?:C\d+)?)?$")


def is_markdown(path: str) -> bool:
    """Whether the path names a Markdown page."""
    return PurePosixPath(path).suffix.lower() in MARKDOWN_SUFFIXES


@dataclass(frozen=True, slots=True)
class Target:
    """A resolved internal link: repository path and optional fragment."""

    path: str
    fragment: str | None


def resolve(source: str, target: str) -> Target | str | None:
    """Resolve a link; None for external links, a message for impossible targets."""
    if SCHEME.match(target):
        return None
    if not target:
        return "empty link target"
    path, _, fragment = target.partition("#")
    path = unquote(path.partition("?")[0])
    if not path:
        return Target(source, unquote(fragment) if fragment else None)
    joined = (
        path.lstrip("/")
        if path.startswith("/")
        else posixpath.join(posixpath.dirname(source), path)
    )
    normal = posixpath.normpath(joined) if joined else "."
    if normal == ".." or normal.startswith("../"):
        return f"'{target}' points outside the repository"
    return Target(normal, unquote(fragment) if fragment else None)


class Corpus:
    """The candidate files with lazily parsed Markdown."""

    def __init__(self, files: Iterable[CandidateFile]) -> None:
        self.files = {file.path: file for file in files}
        self._facts: dict[str, MarkdownFacts | None] = {}

    @cached_property
    def directories(self) -> frozenset[str]:
        """Every directory that contains a candidate file; '.' is the root."""
        found = {"."}
        for path in self.files:
            parent = posixpath.dirname(path)
            while parent and parent not in found:
                found.add(parent)
                parent = posixpath.dirname(parent)
        return frozenset(found)

    @cached_property
    def folded(self) -> dict[str, str]:
        """Case-folded paths, to explain case mismatches."""
        return {path.casefold(): path for path in [*self.files, *self.directories]}

    def facts(self, path: str) -> MarkdownFacts | None:
        """Return the facts of a Markdown file, or None if it is missing or not UTF-8."""
        if path not in self._facts:
            file = self.files.get(path)
            text = file.text() if file is not None and is_markdown(path) else None
            self._facts[path] = parse_markdown(text) if text is not None else None
        return self._facts[path]

    def page_of(self, target: Target) -> str:
        """Return the Markdown file a target shows; a directory shows its README.md."""
        if target.path in self.directories:
            return posixpath.normpath(posixpath.join(target.path, "README.md"))
        return target.path

    def problem(self, target: Target) -> str | None:
        """Explain why a resolved target does not exist, if it does not."""
        if target.path not in self.files and target.path not in self.directories:
            other = self.folded.get(target.path.casefold())
            if other is not None:
                return f"'{target.path}' differs in case from '{other}'"
            return f"'{target.path}' does not exist"
        if target.fragment is None:
            return None
        page = self.page_of(target)
        facts = self.facts(page)
        if facts is None:
            if LINE_ANCHOR.match(target.fragment) and page in self.files:
                return None
            return f"'{target.path}' has no anchors; '#{target.fragment}' cannot resolve"
        if target.fragment not in facts.anchors:
            return f"'{page}' has no heading or anchor '#{target.fragment}'"
        return None

    def linked_pages(self, page: str) -> Iterator[str]:
        """Return the Markdown pages that a page links to."""
        facts = self.facts(page)
        for link in facts.links if facts is not None else []:
            resolved = resolve(page, link.target)
            if isinstance(resolved, Target):
                shown = self.page_of(resolved)
                if self.facts(shown) is not None:
                    yield shown
