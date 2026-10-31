"""Markdown structure linting with PyMarkdown under a configuration owned by the core."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Sequence
from pathlib import Path

from quality.adapters.native import fail, run_tool
from quality.diagnostics import Diagnostic, Location, Rule
from quality.process import Command, sanitized

LINT = Rule(
    id="markdown.lint",
    title="Markdown structure rule violated",
    requirement="R05",
    explanation=(
        "Documentation pages are linted with PyMarkdown's default rules, except line length "
        "(MD013), and with duplicate headings allowed only under different parents (MD024). "
        "The configuration belongs to the core; configuration files in the candidate are "
        "not read."
    ),
    remedy="Fix the reported structure, for example heading levels, list markers, or spacing.",
)
RULES = (LINT,)

CONFIG = {"plugins": {"md013": {"enabled": False}, "md024": {"siblings_only": True}}}
CHUNK = 400
TIMEOUT = 300.0
VIOLATIONS = 4
LINE = re.compile(r"^(\d+)\.md:(\d+):(\d+): (MD\d+): (.*)$")


def _chunks(items: Sequence[str]) -> Iterator[Sequence[str]]:
    for start in range(0, len(items), CHUNK):
        yield items[start : start + CHUNK]


def _relative(line: str, directory: Path) -> str:
    """Strip the scan directory; pymarkdown prints absolute paths for relative arguments."""
    for prefix in {f"{directory}/", f"{directory.resolve()}/"}:
        if line.startswith(prefix):
            return line.removeprefix(prefix)
    return line


def _parse(output: str, paths: Sequence[str], directory: Path) -> Iterator[Diagnostic]:
    for line in output.splitlines():
        if not line.strip():
            continue
        match = LINE.match(_relative(line, directory))
        if match is None:
            fail("pymarkdown", f"unexpected output line: {line}")
        number, row, column, code, message = match.groups()
        yield LINT.diagnostic(
            f"{code}: {message}",
            location=Location(paths[int(number)], int(row), int(column)),
            data={"symbol": code},
        )


def lint_markdown(
    pages: Sequence[tuple[str, bytes]], work: Path, *, python: Path
) -> list[Diagnostic]:
    """Lint copies of the pages under neutral names, from a directory without config."""
    directory = work / "markdown"
    directory.mkdir()
    config = work / "pymarkdown.json"
    config.write_text(json.dumps(CONFIG), encoding="utf-8")
    paths = [path for path, _ in pages]
    for number, (_, data) in enumerate(pages):
        (directory / f"{number}.md").write_bytes(data)
    found: list[Diagnostic] = []
    names = [f"{number}.md" for number in range(len(pages))]
    for chunk in _chunks(names):
        argv = (str(python), "-I", "-m", "pymarkdown", "--config", str(config))
        argv += ("--strict-config", "--return-code-scheme", "explicit", "scan", *chunk)
        command = Command(argv, cwd=directory, timeout=TIMEOUT, env=sanitized())
        completed = run_tool(command, name="pymarkdown", accepted={0, VIOLATIONS})
        if completed.returncode == VIOLATIONS:
            reported = list(_parse(completed.stdout_text(), paths, directory))
            if not reported:
                fail("pymarkdown", "reported violations without any finding")
            found.extend(reported)
    return found
