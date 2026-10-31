"""Inline suppression markers: each one needs a registered exception (R05)."""

from __future__ import annotations

import io
import re
import tokenize
from collections.abc import Iterable, Iterator
from pathlib import PurePosixPath

from quality.diagnostics import Diagnostic, Location, Rule
from quality.policy import Category
from quality.rules.links import is_markdown
from quality.rules.model import CandidateFile

INLINE = Rule(
    id="suppressions.inline",
    title="Inline suppression without a registered exception",
    requirement="R05",
    explanation=(
        "Comments that silence a linter, type checker, formatter, coverage tool, or secret "
        "scanner are exceptions, and so is each fingerprint in a .gitleaksignore file. Each "
        "needs an [[exceptions]] entry with rule "
        "'suppressions.inline', the file path, the marker text as symbol, a reason, an "
        "owner, and an expiry or review date."
    ),
    remedy="Fix the finding instead, or register the exact marker in quality.toml.",
)
RULES = (INLINE,)

PYTHON_MARKERS = re.compile(
    r"\b(?:noqa|type:\s*ignore|pyright:|mypy:|pragma:\s*no\s*(?:cover|branch)|fmt:\s*(?:off|skip)"
    r"|pylint:\s*disable|nosec|ruff:\s*noqa|isort:\s*skip)",
    re.IGNORECASE,
)
SCRIPT_MARKERS = re.compile(
    r"(?:eslint-disable|@ts-ignore|@ts-expect-error|@ts-nocheck|istanbul\s+ignore|c8\s+ignore"
    r"|v8\s+ignore|prettier-ignore|biome-ignore|deno-lint-ignore|oxlint-disable)"
)
DART_MARKERS = re.compile(r"(?:\bignore(?:_for_file)?:|coverage:ignore-|dart\s+format\s+off)")
PHP_MARKERS = re.compile(
    r"(?:@phpstan-ignore|@codeCoverageIgnore|phpcs:(?:ignore|disable)|@psalm-suppress"
    r"|@php-cs-fixer-ignore|@SuppressWarnings|@codingStandardsIgnore|@infection-ignore)"
)
COMMENTED = {
    "typescript": (SCRIPT_MARKERS, re.compile(r"//|/\*|^\s*\*")),
    "javascript": (SCRIPT_MARKERS, re.compile(r"//|/\*|^\s*\*")),
    "dart": (DART_MARKERS, re.compile(r"//|/\*|^\s*\*")),
    "php": (PHP_MARKERS, re.compile(r"//|#|/\*|^\s*\*")),
}
# The stylesheets and HTML of a browser component, which Stylelint and Prettier check.
STYLED = {
    ".css": (re.compile(r"(?:stylelint-disable|prettier-ignore)"), re.compile(r"/\*|^\s*\*")),
    ".html": (re.compile(r"prettier-ignore"), re.compile(r"<!--")),
}
PYTHON_FALLBACK = (PYTHON_MARKERS, re.compile(r"#"))
MARKDOWN_MARKERS = (re.compile(r"pyml\s+disable", re.IGNORECASE), re.compile(r"<!--"))
# A character class keeps this line from carrying the marker that the scanner honors.
SCANNER_MARKER = re.compile(r"gitleaks[:]allow")
SCANNER_IGNORE_FILE = ".gitleaksignore"
TRAILER = re.compile(r"\s*(?:\*/|-->)\s*$")
CHECKED_CATEGORIES = frozenset({Category.SOURCE, Category.TESTS, Category.CONFIG})


def _symbol(text: str) -> str:
    return " ".join(TRAILER.sub("", text).split())


def _python(source: bytes) -> Iterator[tuple[int, str]]:
    """Markers in comment tokens; a file that cannot be tokenized is scanned line by line."""
    tokens = tokenize.tokenize(io.BytesIO(source).readline)
    try:
        for token in tokens:
            if token.type == tokenize.COMMENT and (found := PYTHON_MARKERS.search(token.string)):
                yield token.start[0], _symbol(token.string[found.start() :])
    except tokenize.TokenError, SyntaxError:
        yield from _commented(source.decode("utf-8", errors="replace"), PYTHON_FALLBACK)


def _commented(
    source: str, syntax: tuple[re.Pattern[str], re.Pattern[str]]
) -> Iterator[tuple[int, str]]:
    markers, comment = syntax
    for number, line in enumerate(source.split("\n"), start=1):
        opened = comment.search(line)
        if opened is not None and (found := markers.search(line, opened.start())):
            yield number, _symbol(line[found.start() :])


def _fingerprints(text: str) -> Iterator[tuple[int, str]]:
    for number, line in enumerate(text.split("\n"), start=1):
        entry = line.strip()
        if entry and not entry.startswith("#"):
            yield number, entry


def _syntax(
    file: CandidateFile, language: str | None, *, styled: bool
) -> tuple[re.Pattern[str], re.Pattern[str]] | None:
    """Return the marker and comment patterns of a file that is not Python."""
    if language in COMMENTED:
        return COMMENTED[language]
    if file.category is Category.DOCS and is_markdown(file.path):
        return MARKDOWN_MARKERS
    return STYLED.get(PurePosixPath(file.path).suffix.lower()) if styled else None


def _tool_markers(file: CandidateFile, data: bytes, *, styled: bool) -> Iterator[tuple[int, str]]:
    language = file.record.language if file.category in CHECKED_CATEGORIES else None
    if language == "python":
        yield from _python(data)
    elif (syntax := _syntax(file, language, styled=styled)) is not None:
        yield from _commented(data.decode("utf-8", errors="replace"), syntax)


def markers(file: CandidateFile, *, styled: bool = False) -> Iterator[tuple[int, str]]:
    """Yield the line and symbol of each suppression marker in one file.

    Tool markers count in code that tools check (source, tests, configuration),
    for the Markdown linter in documentation, and, when the file is `styled`
    because a browser component owns it, for Stylelint and Prettier in CSS and
    HTML. Secret scanner markers count everywhere, because other scanner runs
    honor them everywhere. Scanner ignore files are lists of suppressed
    findings, so each entry counts too.
    """
    if file.data is None:
        return
    yield from _tool_markers(file, file.data, styled=styled)
    text = file.data.decode("utf-8", errors="replace")
    if PurePosixPath(file.path).name == SCANNER_IGNORE_FILE:
        yield from _fingerprints(text)
    for number, line in enumerate(text.split("\n"), start=1):
        if (found := SCANNER_MARKER.search(line)) is not None:
            yield number, found.group(0)


def check_suppressions(
    files: Iterable[CandidateFile], styled: frozenset[str] = frozenset()
) -> Iterator[Diagnostic]:
    """Report every inline suppression marker; exceptions remove the registered ones.

    `styled` names the browser components, whose CSS and HTML carry markers too.
    """
    for file in files:
        seen: set[tuple[int, str]] = set()
        for line, symbol in markers(file, styled=file.record.component in styled):
            if (line, symbol) in seen:
                continue
            seen.add((line, symbol))
            yield INLINE.diagnostic(
                f"inline suppression '{symbol}' needs a registered exception",
                location=Location(file.path, line),
                component=file.record.component,
                data={"symbol": symbol},
            )
