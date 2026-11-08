"""Ruff, the formatter and linter of the Python profile.

Both run with the pinned Ruff on explicit file lists and the component's own
configuration file. `--no-force-exclude` makes exclusions in that file
irrelevant for the files the gate names, and nothing is fixed or cached.
"""

from __future__ import annotations

from pathlib import Path

from quality.adapters.native import fail, run_tool
from quality.adapters.settings import (
    MAX_EXTENDS,
    Report,
    Table,
    inside,
    label,
    load_toml,
    strings,
    table,
)
from quality.adapters.target import Entry, Target, document, findings, location, number
from quality.boundedio import ReportError
from quality.diagnostics import Diagnostic, Rule
from quality.process import Command, sanitized

FORMAT = Rule(
    id="python.format",
    title="Python file is not formatted by the pinned Ruff",
    requirement="R04",
    explanation=(
        "Source and test files are checked with `ruff format --check` and the component's "
        "Ruff configuration. Exclusions in that configuration do not apply to the checked files."
    ),
    remedy="Run `./quality fix`, review the change, and stage it.",
)
LINT = Rule(
    id="python.lint",
    title="Ruff lint finding",
    requirement="R04",
    explanation=(
        "Source and test files are checked with `ruff check` and the component's explicit rule "
        "selection. Fixes are never applied during a check, and exclusions do not apply."
    ),
    remedy="Fix the finding; `./quality fix` applies Ruff's safe fixes to the working tree.",
)
RULES = (FORMAT, LINT)

TIMEOUT = 600.0
RUFF_FILES = (".ruff.toml", "ruff.toml")
BLANKET = "ALL"
IGNORE_KEYS = ("ignore", "extend-ignore")
PER_FILE_KEYS = ("per-file-ignores", "extend-per-file-ignores")
SYNTAX = "invalid-syntax"


def _load(path: Path) -> Table | None:
    loaded = load_toml(path)
    return table(loaded, "tool", "ruff") if path.name == "pyproject.toml" else loaded


def _selects(settings: Table) -> bool:
    lint = table(settings, "lint") or {}
    return bool(strings(lint.get("select", settings.get("select"))))


def _per_file(files: Table, where: str, add: Report) -> None:
    for pattern, codes in files.items():
        if BLANKET in strings(codes):
            add(where, f"Ruff per-file ignores must not exempt {pattern!r} from ALL rules")


def _blanket(settings: Table, where: str, add: Report) -> None:
    scopes = (settings, table(settings, "lint") or {})
    if any(BLANKET in strings(scope.get(key)) for scope in scopes for key in IGNORE_KEYS):
        add(where, "Ruff must not ignore ALL rules")
    for scope in scopes:
        for key in PER_FILE_KEYS:
            _per_file(table(scope, key) or {}, where, add)


def ruff_settings(directory: Path, snapshot: Path, add: Report) -> str:
    """Check the Ruff configuration and its `extend` chain; return the file Ruff reads."""
    name = next((file for file in RUFF_FILES if (directory / file).exists()), "pyproject.toml")
    path, selected = directory / name, False
    for _ in range(MAX_EXTENDS + 1):
        where = label(path, directory)
        try:
            settings = _load(path)
        except ReportError as error:
            add(where, str(error))
            return name
        if settings is None:
            break
        _blanket(settings, where, add)
        selected = selected or _selects(settings)
        parent = settings.get("extend")
        if not isinstance(parent, str):
            break
        if not inside(path.parent, parent, snapshot):
            add(where, f"Ruff 'extend = {parent!r}' points outside the repository")
            return name
        path = (path.parent / parent).resolve()
    else:
        add(name, f"Ruff 'extend' chain is circular or longer than {MAX_EXTENDS} files")
        return name
    if not selected:
        add(name, "select Ruff lint rules explicitly with lint.select")
    return name


def _diagnostic(rule: Rule, target: Target, entry: Entry, message: str) -> Diagnostic:
    path = target.relative("ruff", entry.get("filename"))
    position = entry.get("location")
    found = position if isinstance(position, dict) else {}
    where = location(path, number(found, "row"), number(found, "column"))
    return rule.diagnostic(message, location=where, component=target.component.id)


def _run(target: Target, ruff: Path, argv: tuple[str, ...], accepted: set[int]) -> list[Entry]:
    command = Command(
        (str(ruff), *argv, "--no-cache", *target.arguments()),
        cwd=target.directory,
        timeout=TIMEOUT,
        env=sanitized(),
    )
    completed = run_tool(command, name="ruff", accepted=accepted)
    found = findings("ruff", document("ruff", completed))
    if bool(found) != (completed.returncode != 0):
        detail = completed.stderr_text().strip()
        fail("ruff", f"exit status {completed.returncode} with {len(found)} findings: {detail}")
    return found


def ruff_format(target: Target, *, ruff: Path, config: str) -> list[Diagnostic]:
    """Report files the formatter would change; syntax errors are left to the linter."""
    argv = (
        "format",
        "--check",
        "--output-format",
        "json",
        "--config",
        str(target.directory / config),
    )
    found = _run(target, ruff, (*argv, "--no-force-exclude"), {0, 1, 2})
    message = "the file is not formatted; run `./quality fix`"
    return [_diagnostic(FORMAT, target, e, message) for e in found if e.get("code") != SYNTAX]


def ruff_lint(target: Target, *, ruff: Path, config: str) -> list[Diagnostic]:
    """Report lint findings, including syntax errors."""
    argv = ("check", "--output-format", "json", "--config", str(target.directory / config))
    found = _run(target, ruff, (*argv, "--no-force-exclude", "--no-fix"), {0, 1})
    return [
        _diagnostic(LINT, target, entry, f"{entry.get('code')}: {entry.get('message')}")
        for entry in found
    ]
