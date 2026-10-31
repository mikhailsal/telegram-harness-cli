"""Prettier, the formatter of the TypeScript profile.

The pinned Prettier checks explicit file lists with `--list-different`: the
TypeScript and configuration scripts and, in a browser component, its CSS and
HTML entry points. An empty ignore file replaces `.gitignore` and
`.prettierignore`, so no file the gate names can be skipped. A file Prettier
cannot parse makes it exit 2 for the whole run; the others are checked again
without it. Scripts and stylesheets are left to the type checker, ESLint, and
Stylelint, which report the syntax error; an HTML file has no other parser, so
its error is reported here.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

from quality.adapters.native import fail, run_tool
from quality.adapters.target import Target
from quality.adapters.typescript.environment import Node
from quality.diagnostics import Diagnostic, Location, Rule
from quality.process import Command

FORMAT = Rule(
    id="typescript.format",
    title="TypeScript file is not formatted by the pinned Prettier",
    requirement="R04",
    explanation=(
        "Source, test, and configuration scripts, and the CSS and HTML entry points of a "
        "browser component, are checked with `prettier --list-different` and the component's "
        "Prettier configuration. Ignore files do not apply to them."
    ),
    remedy="Run `./quality fix`, review the change, and stage it.",
)
RULES = (FORMAT,)

TIMEOUT = 600.0
TOOL = "prettier"
SYNTAX = re.compile(r"^\[error\] (.+?): SyntaxError: (.*)$", re.MULTILINE)
UNCHECKED = frozenset({".html", ".htm"})


def prettier_argv(node: Node, script: Path, ignore: Path, *flags: str) -> tuple[str, ...]:
    """Build a Prettier command that honors no ignore file."""
    options = ("--ignore-path", str(ignore), "--no-error-on-unmatched-pattern")
    return (str(node.executable), str(script), *flags, *options)


def _unparsable(target: Target, stderr: str) -> dict[str, str]:
    errors: list[tuple[str, str]] = SYNTAX.findall(stderr)
    return {target.relative(TOOL, name): message.strip() for name, message in errors}


def _syntax(target: Target, skipped: dict[str, str]) -> list[Diagnostic]:
    return [
        FORMAT.diagnostic(
            f"Prettier cannot parse the file: {message}",
            location=Location(path),
            component=target.component.id,
        )
        for path, message in skipped.items()
        if PurePosixPath(path).suffix.lower() in UNCHECKED
    ]


def prettier_format(target: Target, *, node: Node, script: Path, home: Path) -> list[Diagnostic]:
    """Report each named file whose formatting differs from Prettier's."""
    ignore = home / "prettierignore"
    home.mkdir(parents=True, exist_ok=True)
    ignore.write_text("", encoding="utf-8")
    files: tuple[str, ...] = target.files
    found: list[Diagnostic] = []
    while files:
        current = Target(target.component, target.snapshot, files)
        command = Command(
            (*prettier_argv(node, script, ignore, "--list-different"), *current.arguments()),
            cwd=target.directory,
            timeout=TIMEOUT,
            env=node.environ(home),
        )
        completed = run_tool(command, name=TOOL, accepted={0, 1, 2})
        if completed.returncode != 2:
            listed = completed.stdout_text().splitlines()
            return found + [
                FORMAT.diagnostic(
                    "the file is not formatted; run `./quality fix`",
                    location=Location(current.relative(TOOL, name)),
                    component=target.component.id,
                )
                for name in listed
                if name.strip()
            ]
        skipped = _unparsable(current, completed.stderr_text())
        if not skipped.keys() & set(files):
            fail(TOOL, f"exit status 2: {completed.stderr_text()}")
        found += _syntax(target, skipped)
        files = tuple(path for path in files if path not in skipped)
    return found
