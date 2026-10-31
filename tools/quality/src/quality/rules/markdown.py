"""Parse Markdown once into the facts the documentation rules need."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field

from markdown_it import MarkdownIt
from markdown_it.token import Token

HTML_ANCHOR = re.compile(r"""\b(?:id|name)\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.IGNORECASE)
HTML_LINK = re.compile(r"""\b(?:href|src)\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.IGNORECASE)
SHELL_FENCES = frozenset({"", "sh", "bash", "shell", "console", "zsh", "shell-session"})
COMMAND = re.compile(r"(?:^|[\s;&|(`])(?:\./)?quality((?:\s+[^\s;&|)`]+)+)")


@dataclass(frozen=True, slots=True)
class Link:
    """A link or image target as written, with its 1-based line."""

    target: str
    line: int


@dataclass(frozen=True, slots=True)
class Command:
    """A `quality` invocation found in code: the words after the program name."""

    words: tuple[str, ...]
    line: int


@dataclass(slots=True)
class MarkdownFacts:
    """Anchors, links, prose, and command mentions of one Markdown page."""

    anchors: set[str] = field(default_factory=set[str])
    links: list[Link] = field(default_factory=list[Link])
    commands: list[Command] = field(default_factory=list[Command])
    prose: list[tuple[int, str]] = field(default_factory=list[tuple[int, str]])

    @property
    def prose_words(self) -> int:
        """Words of prose text; code, HTML, and bare URLs are not prose."""
        return sum(count_words(text) for _, text in self.prose)


def count_words(text: str) -> int:
    """Count whitespace-separated tokens that contain a letter or digit."""
    return sum(1 for token in text.split() if any(ch.isalnum() for ch in token))


def slug(text: str) -> str:
    """Return the GitHub heading anchor: lower case, no punctuation, hyphens for spaces."""
    kept = (
        ch for ch in text.lower() if ch in "-_ " or unicodedata.category(ch)[0] in {"L", "M", "N"}
    )
    return "".join(kept).replace(" ", "-")


def _commands(code: str, line: int) -> Iterator[Command]:
    for offset, text in enumerate(code.splitlines()):
        for match in COMMAND.finditer(text):
            yield Command(tuple(match.group(1).split()), line + offset)


class _Collector:
    def __init__(self) -> None:
        self.facts = MarkdownFacts()
        self.slugs: dict[str, int] = {}

    def heading(self, inline: Token) -> None:
        text = "".join(
            child.content
            for child in inline.children or []
            if child.type in {"text", "code_inline"}
        )
        base = slug(text)
        result = base
        while result in self.slugs:
            self.slugs[base] += 1
            result = f"{base}-{self.slugs[base]}"
        self.slugs[result] = 0
        self.facts.anchors.add(result)

    def html(self, content: str, line: int) -> None:
        for match in HTML_ANCHOR.finditer(content):
            self.facts.anchors.add(match.group(1) or match.group(2) or "")
        for offset, text in enumerate(content.splitlines()):
            for match in HTML_LINK.finditer(text):
                self.facts.links.append(Link(match.group(1) or match.group(2) or "", line + offset))

    def inline(self, inline: Token, line: int) -> None:
        in_autolink = False
        for child in inline.children or []:
            match child.type:
                case "softbreak" | "hardbreak":
                    line += 1
                case "link_open":
                    in_autolink = child.markup == "autolink"
                    self.facts.links.append(Link(str(child.attrs.get("href", "")), line))
                case "link_close":
                    in_autolink = False
                case "image":
                    self.facts.links.append(Link(str(child.attrs.get("src", "")), line))
                case "code_inline":
                    self.facts.commands.extend(_commands(child.content, line))
                case "html_inline":
                    self.html(child.content, line)
                case "text" if not in_autolink:
                    self.facts.prose.append((line, child.content))
                case _:
                    pass

    def block(self, tokens: Sequence[Token], position: int) -> None:
        token = tokens[position]
        line = (token.map[0] if token.map else 0) + 1
        match token.type:
            case "heading_open":
                self.heading(tokens[position + 1])
            case "inline":
                self.inline(token, line)
            case "fence" if token.info.strip().split(" ")[0] in SHELL_FENCES:
                self.facts.commands.extend(_commands(token.content, line + 1))
            case "html_block":
                self.html(token.content, line)
            case _:
                pass


def parse_markdown(text: str) -> MarkdownFacts:
    """Collect anchors, links, prose, and commands with CommonMark and GFM tables."""
    tokens = MarkdownIt("commonmark").enable("table").parse(text)
    collector = _Collector()
    for position in range(len(tokens)):
        collector.block(tokens, position)
    return collector.facts
