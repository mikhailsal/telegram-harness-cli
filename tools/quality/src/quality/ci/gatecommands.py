"""Recognize direct, unconditional gate commands without executing shell input.

This is a bounded recognizer, not a shell interpreter. Quoted data and comments
never become commands. Compound gate commands and opaque shell constructs cannot
prove lane coverage; the ordinary wrapper may prepare arguments in an if block
before its unconditional, direct gate invocation.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field

CALL = re.compile(r"^\s*(?:\./)?quality[ \t]+gate(?:[ \t]+(.*))?$", re.DOTALL)
BLOCKS = frozenset({"if", "for", "while", "until", "case", "select"})
OPAQUE = frozenset({"exit", "return", "exec", "eval", "source", ".", "alias", "function", "{"})
# Only the shipped case block's labels, positional arguments, stderr logging,
# and nonzero failures are recognized as compound argument preparation.
PREPARATION = {
    ")": re.compile(r"[^\s()]+\)"),
    ";;": re.compile(r"(?:set -- .*;;|exit (?:[1-9]|[1-9][0-9]|1[0-9]{2}|2[0-4][0-9]|25[0-5]) ;;)"),
    ">&": re.compile(r"echo .*>&2"),
}


@dataclass(slots=True)
class _Line:
    text: list[str] = field(default_factory=list[str])
    operators: str = ""
    quote: str = ""
    escaped: bool = False
    comment: bool = False

    def _escape(self, char: str) -> bool:
        self.escaped = False
        if char == "\n":
            self.text.pop()
        else:
            self.text.append(char)
        return False

    def _comment_start(self, char: str) -> bool:
        boundary = not self.text or self.text[-1].isspace()
        return char == "#" and not self.quote and boundary

    def _syntax(self, char: str) -> None:
        if char in ("'", '"'):
            self.quote = "" if self.quote == char else self.quote or char
        if char == "`" and self.quote != "'":
            self.operators += char
        if not self.quote and char in ";&|<>()":
            self.operators += char

    def read(self, char: str) -> bool:
        if self.escaped:
            return self._escape(char)
        if self.comment:
            return char == "\n"
        if char == "\\" and self.quote != "'":
            self.escaped = True
            self.text.append(char)
            return False
        if self._comment_start(char):
            self.comment = True
            return False
        self._syntax(char)
        if char == "\n" and not self.quote:
            return True
        self.text.append(char)
        return False


def _lines(script: str) -> Iterator[tuple[str, str]]:
    line = _Line()
    for char in script + "\n":
        if line.read(char):
            yield "".join(line.text), line.operators
            line = _Line()
    if line.text:
        # An unterminated quote cannot make its contents a new command.
        yield "".join(line.text), line.operators + "?"


def _preparation(line: str, operators: str, depth: int) -> bool:
    pattern = PREPARATION.get(operators)
    return bool(depth and pattern is not None and pattern.fullmatch(line))


def _condition(words: list[str], operators: str) -> bool:
    return bool(
        words and words[0] in BLOCKS | {"elif"} and operators == ";" and words[-1] in ("then", "do")
    )


def _opaque(first: str, operators: str, *, preparation: bool) -> bool:
    return not preparation and (first in OPAQUE or bool(operators))


@dataclass(slots=True)
class _Context:
    depth: int = 0
    opaque: bool = False

    def observe(self, line: str, operators: str) -> None:
        words = line.split()
        first = words[0].rstrip(";") if words else ""
        if first in ("fi", "done", "esac"):
            self.depth = max(0, self.depth - 1)
        preparation = _condition(words, operators) or _preparation(line, operators, self.depth)
        self.opaque = self.opaque or _opaque(first, operators, preparation=preparation)
        if first in BLOCKS:
            self.depth += 1

    def direct(self, operators: str) -> bool:
        return not (self.depth or self.opaque or operators)


def gate_arguments(script: str) -> Iterator[str | None]:
    """Yield direct gate argument text, or None for a call whose execution is opaque."""
    context = _Context()
    for text, operators in _lines(script):
        line = text.strip()
        context.observe(line, operators)
        call = CALL.fullmatch(line)
        if call is not None:
            yield (call.group(1) or "") if context.direct(operators) else None
