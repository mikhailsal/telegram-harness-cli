"""Pyright in strict mode, the type checker of the Python profile.

The settings check walks `pyrightconfig.json` or `[tool.pyright]` and every file
it extends. The gate passes explicit files and the component's locked
interpreter; a file the configuration keeps out of analysis fails.
"""

from __future__ import annotations

import posixpath
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from quality.adapters.native import fail, run_tool
from quality.adapters.settings import (
    MAX_EXTENDS,
    Report,
    Table,
    inside,
    label,
    load_json_object,
    load_toml,
    strings,
    table,
)
from quality.adapters.target import Entry, Target, document, findings, location, number
from quality.boundedio import JsonValue, ReportError
from quality.diagnostics import Diagnostic, Location, Rule, Severity
from quality.process import Command, sanitized

TYPES = Rule(
    id="python.types",
    title="Pyright strict type error",
    requirement="R04",
    explanation=(
        "Source and test files are checked by the pinned Pyright in strict mode against the "
        "component's locked environment. A file the configuration excludes from analysis fails."
    ),
    remedy="Fix the type error; do not weaken the configuration or add ignore comments.",
)
RULES = (TYPES,)

TIMEOUT = 900.0
PYRIGHT_FILE = "pyrightconfig.json"
WEAK = frozenset({"none", "information", "warning"})
# Settings that strict mode turns on; switching one off weakens the profile.
STRICT_SWITCHES = (
    "analyzeUnannotatedFunctions",
    "disableBytesTypePromotions",
    "strictDictionaryInference",
    "strictListInference",
    "strictParameterNoneValue",
    "strictSetInference",
)
FORBIDDEN = {
    "ignore": "it hides diagnostics in matching files",
    "defineConstant": "it can make code unreachable and therefore unchecked",
}
PATHS = ("root", "stubPath", "typeshedPath", "venvPath")
SEVERITIES = {"error": Severity.ERROR, "warning": Severity.WARNING}


def _load(path: Path) -> Table | None:
    if path.suffix == ".toml":
        return table(load_toml(path), "tool", "pyright")
    return load_json_object(path)


def _scopes(settings: Table) -> list[Table]:
    """Return the top-level settings and each execution environment that overrides them."""
    value = settings.get("executionEnvironments")
    items = cast(list[object], value) if isinstance(value, list) else []
    return [settings, *(cast(Table, item) for item in items if isinstance(item, Mapping))]


def _weakened(scope: Table) -> list[str]:
    found = [
        f"{key} = {value!r}"
        for key, value in scope.items()
        if key.startswith("report")
        and (value is False or (isinstance(value, str) and value in WEAK))
    ]
    found.extend(f"{key} = false" for key in STRICT_SWITCHES if scope.get(key) is False)
    return found


def _outside(scope: Table, base: Path, snapshot: Path) -> list[str]:
    values = [value for key in PATHS if isinstance(value := scope.get(key), str)]
    values.extend(strings(scope.get("extraPaths")))
    return [value for value in values if not inside(base, value, snapshot)]


def _tracked_venv(settings: Table, base: Path) -> str | None:
    venv, venv_path = settings.get("venv"), settings.get("venvPath", ".")
    if isinstance(venv, str) and isinstance(venv_path, str) and (base / venv_path / venv).exists():
        return venv
    return None


def _check(settings: Table, base: Path, snapshot: Path, where: str, add: Report) -> None:
    for key, reason in FORBIDDEN.items():
        if key in settings:
            add(where, f"Pyright '{key}' is not allowed: {reason}")
    for scope in _scopes(settings):
        for setting in _weakened(scope):
            add(where, f"Pyright '{setting}' weakens strict mode")
        for value in _outside(scope, base, snapshot):
            add(where, f"Pyright path {value!r} points outside the repository")
    venv = _tracked_venv(settings, base)
    if venv is not None:
        add(where, f"Pyright venv {venv!r} is tracked; it would replace the locked environment")


def pyright_settings(directory: Path, snapshot: Path, add: Report) -> str:
    """Check the Pyright configuration and its `extends` chain; return the file Pyright reads."""
    name = PYRIGHT_FILE if (directory / PYRIGHT_FILE).exists() else "pyproject.toml"
    path, mode = directory / name, None
    for _ in range(MAX_EXTENDS + 1):
        where = label(path, directory)
        try:
            settings = _load(path)
        except ReportError as error:
            add(where, str(error))
            return name
        if settings is None:
            break
        _check(settings, path.parent, snapshot, where, add)
        if mode is None and isinstance(settings.get("typeCheckingMode"), str):
            mode = settings.get("typeCheckingMode")
        parent = settings.get("extends")
        if not isinstance(parent, str):
            break
        if not inside(path.parent, parent, snapshot):
            add(where, f"Pyright 'extends = {parent!r}' points outside the repository")
            return name
        path = (path.parent / parent).resolve()
    else:
        add(name, f"Pyright 'extends' chain is circular or longer than {MAX_EXTENDS} files")
        return name
    if mode != "strict":
        add(name, 'run Pyright with typeCheckingMode = "strict"')
    return name


def _diagnostic(target: Target, entry: Entry) -> Diagnostic | None:
    severity = SEVERITIES.get(str(entry.get("severity")))
    if severity is None:
        return None
    path = target.relative("pyright", entry.get("file"))
    span = entry.get("range")
    start = span.get("start") if isinstance(span, dict) else None
    found = start if isinstance(start, dict) else {}
    line, column = number(found, "line"), number(found, "character")
    where = location(
        path, None if line is None else line + 1, None if column is None else column + 1
    )
    rule = entry.get("rule")
    # Pyright indents its explanation on further lines; one line keeps it readable.
    text = "; ".join(
        filter(None, (part.strip() for part in str(entry.get("message")).splitlines()))
    )
    message = f"{text} ({rule})" if isinstance(rule, str) else text
    return TYPES.diagnostic(
        message, location=where, component=target.component.id, severity=severity
    )


def _summary(document: JsonValue) -> dict[str, JsonValue]:
    summary = document.get("summary") if isinstance(document, dict) else None
    if not isinstance(summary, dict):
        fail("pyright", "output has no summary")
    return summary


def pyright_types(target: Target, *, pyright: Path, config: str, python: Path) -> list[Diagnostic]:
    """Type-check the target files; return errors, warnings, and unanalyzed files."""
    argv = (str(pyright), "--outputjson", "--project", str(target.directory / config))
    argv += ("--pythonpath", str(python), *target.arguments())
    command = Command(argv, cwd=target.directory, timeout=TIMEOUT, env=sanitized())
    completed = run_tool(command, name="pyright", accepted={0, 1})
    output = document("pyright", completed)
    listed = output.get("generalDiagnostics") if isinstance(output, dict) else None
    found = [d for e in findings("pyright", listed) if (d := _diagnostic(target, e))]
    summary = _summary(output)
    errors = sum(1 for diagnostic in found if diagnostic.severity is Severity.ERROR)
    if number(summary, "errorCount") != errors or (completed.returncode == 1) != (errors > 0):
        fail("pyright", f"exit status {completed.returncode} disagrees with {errors} errors")
    analyzed = number(summary, "filesAnalyzed")
    if analyzed is None:
        fail("pyright", "summary has no filesAnalyzed count")
    if analyzed != len(target.files):
        message = f"Pyright analyzed {analyzed} of {len(target.files)} files; do not exclude them"
        where = Location(posixpath.normpath(posixpath.join(target.component.root, config)))
        found.append(TYPES.diagnostic(message, location=where, component=target.component.id))
    return found
