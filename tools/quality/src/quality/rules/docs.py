"""Documentation rules: page size, agent instructions, links, index, language, commands."""

from __future__ import annotations

import re
import unicodedata
from collections import deque
from collections.abc import Iterable, Iterator, Mapping
from pathlib import PurePosixPath

from quality.diagnostics import Diagnostic, Location, Rule, Severity
from quality.policy import Category, Thresholds
from quality.rules.links import MARKDOWN_SUFFIXES, Corpus, resolve
from quality.rules.markdown import Link, MarkdownFacts, count_words
from quality.rules.model import CandidateFile, physical_lines

PAGE_SIZE = Rule(
    id="docs.page-size",
    title="Documentation page is too long",
    requirement="R05",
    explanation=(
        "Authored pages may have at most thresholds.doc_page_lines physical lines and at "
        "most thresholds.doc_page_words prose words; code blocks and HTML blocks are not prose."
    ),
    remedy="Split the page by topic and link the parts from an index.",
)
AGENTS_SIZE = Rule(
    id="docs.agents-size",
    title="Agent instructions are too long",
    requirement="R05",
    explanation=(
        "Root AGENTS.md and CLAUDE.md are loaded into every agent session. They hold setup, "
        "checks, boundaries, links, and the exception procedure within thresholds.agents_lines "
        "and thresholds.agents_words; thresholds themselves stay in quality.toml."
    ),
    remedy="Move details into linked documentation pages.",
)
AGENTS_MISSING = Rule(
    id="docs.agents-missing",
    title="Root AGENTS.md is missing",
    requirement="R05",
    explanation="Agents need one concise root instruction file that links to the policy.",
    remedy="Add AGENTS.md with setup, checks, boundaries, links, and exception procedure.",
)
BROKEN_LINK = Rule(
    id="docs.broken-link",
    title="Internal link or anchor does not resolve",
    requirement="R05",
    explanation=(
        "Relative links, root-relative links, and anchors are resolved against the candidate "
        "snapshot with exact, case-sensitive paths and GitHub heading anchors."
    ),
    remedy="Fix the path or anchor, or add the missing target to the change.",
)
UNINDEXED = Rule(
    id="docs.unindexed",
    title="Documentation page is not reachable from the index",
    requirement="R05",
    explanation=(
        "Every Markdown page outside hidden directories must be reachable through internal "
        "links from the root README.md or AGENTS.md."
    ),
    remedy="Link the page from an index page.",
)
NON_ENGLISH = Rule(
    id="docs.non-english",
    title="Documentation prose may not be English",
    requirement="R05",
    explanation=(
        "Documentation is written in English. Detection is advisory: a paragraph written "
        "mostly in non-Latin script is reported as a warning. Non-ASCII text is allowed."
    ),
    remedy="Write the text in English, or keep foreign-language quotes short.",
    severity=Severity.WARNING,
)
UNKNOWN_COMMAND = Rule(
    id="docs.unknown-command",
    title="Documented command does not exist",
    requirement="R05",
    explanation="Commands written in code spans and shell blocks must exist in the CLI.",
    remedy="Correct the command or document it once it is implemented.",
)
RULES = (
    PAGE_SIZE,
    AGENTS_SIZE,
    AGENTS_MISSING,
    BROKEN_LINK,
    UNINDEXED,
    NON_ENGLISH,
    UNKNOWN_COMMAND,
)

PAGE_SUFFIXES = MARKDOWN_SUFFIXES | {".rst", ".adoc", ".txt"}
LEGAL_STEMS = frozenset({"LICENSE", "LICENCE", "COPYING", "NOTICE"})
AGENT_FILES = ("AGENTS.md", "CLAUDE.md")
INDEX_ROOTS = ("README.md", *AGENT_FILES)
SCRIPT_MIN_LETTERS = 40
PLACEHOLDER = re.compile(r"<[^>]*>|\[[^\]]*\]|\{[^}]*\}|\.\.\.|\$\S*")

type Commands = Mapping[str, frozenset[str]]


def is_page(file: CandidateFile) -> bool:
    """Return whether a file is an authored page: documentation other than legal notices."""
    pure = PurePosixPath(file.path)
    return (
        file.category is Category.DOCS
        and pure.suffix.lower() in PAGE_SUFFIXES
        and pure.stem.upper() not in LEGAL_STEMS
    )


def _broken_links(corpus: Corpus, path: str, links: list[Link]) -> Iterator[Diagnostic]:
    for link in links:
        resolved = resolve(path, link.target)
        if resolved is None:
            continue
        message = resolved if isinstance(resolved, str) else corpus.problem(resolved)
        if message is not None:
            yield BROKEN_LINK.diagnostic(message, location=Location(path, link.line))


def _limits(path: str, thresholds: Thresholds) -> tuple[int, int, Rule]:
    if path in AGENT_FILES:
        return thresholds.agents_lines, thresholds.agents_words, AGENTS_SIZE
    return thresholds.doc_page_lines, thresholds.doc_page_words, PAGE_SIZE


def _size(
    file: CandidateFile, facts: MarkdownFacts | None, limits: tuple[int, int, Rule]
) -> Iterator[Diagnostic]:
    lines_max, words_max, rule = limits
    words = facts.prose_words if facts is not None else count_words(file.text() or "")
    lines = physical_lines(file.data or b"")
    if lines > lines_max or words > words_max:
        message = f"{lines} lines (max {lines_max}) and {words} prose words (max {words_max})"
        yield rule.diagnostic(message, location=Location(file.path))


def _latin_share(text: str) -> tuple[int, int]:
    letters = [ch for ch in text if ch.isalpha()]
    latin = sum(1 for ch in letters if unicodedata.name(ch, "").startswith("LATIN"))
    return latin, len(letters)


def _language(path: str, facts: MarkdownFacts) -> Iterator[Diagnostic]:
    for line, text in facts.prose:
        latin, letters = _latin_share(text)
        if letters >= SCRIPT_MIN_LETTERS and latin * 2 < letters:
            message = "prose is mostly in a non-Latin script; documentation is in English"
            yield NON_ENGLISH.diagnostic(message, location=Location(path, line))
            return


def _word(text: str) -> bool:
    """Whether a word names a command rather than an option or a placeholder."""
    return not text.startswith("-") and PLACEHOLDER.fullmatch(text) is None


def _commands(path: str, facts: MarkdownFacts, commands: Commands) -> Iterator[Diagnostic]:
    for command in facts.commands:
        first, *rest = command.words
        if not _word(first):
            continue
        sub = rest[0] if rest and _word(rest[0]) else None
        if first not in commands:
            message = f"'quality {first}' is not a command"
        elif sub is not None and commands[first] and sub not in commands[first]:
            message = f"'quality {first} {sub}' is not a command"
        else:
            continue
        yield UNKNOWN_COMMAND.diagnostic(message, location=Location(path, command.line))


def _reachable(corpus: Corpus) -> set[str]:
    queue = deque(root for root in INDEX_ROOTS if root in corpus.files)
    seen = set(queue)
    while queue:
        for shown in corpus.linked_pages(queue.popleft()):
            if shown not in seen:
                seen.add(shown)
                queue.append(shown)
    return seen


def _hidden(path: str) -> bool:
    return any(part.startswith(".") for part in PurePosixPath(path).parts[:-1])


def _page_rules(
    corpus: Corpus, path: str, commands: Commands, reachable: set[str]
) -> Iterator[Diagnostic]:
    facts = corpus.facts(path)
    if facts is None:
        return
    yield from _broken_links(corpus, path, facts.links)
    yield from _language(path, facts)
    yield from _commands(path, facts, commands)
    if path not in reachable and not _hidden(path):
        yield UNINDEXED.diagnostic(
            "page is not linked from README.md, AGENTS.md, or a linked page",
            location=Location(path),
        )


def check_docs(
    files: Iterable[CandidateFile], thresholds: Thresholds, commands: Commands
) -> Iterator[Diagnostic]:
    """Apply every documentation rule to the candidate."""
    corpus = Corpus(files)
    if "AGENTS.md" not in corpus.files:
        yield AGENTS_MISSING.diagnostic("the repository root has no AGENTS.md")
    reachable = _reachable(corpus)
    for path, file in sorted(corpus.files.items()):
        if is_page(file) and file.text() is not None:
            yield from _size(file, corpus.facts(path), _limits(path, thresholds))
            yield from _page_rules(corpus, path, commands, reachable)
