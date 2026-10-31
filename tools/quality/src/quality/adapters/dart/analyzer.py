"""`dart analyze`, the linter and type checker of the Dart profile.

The analyzer checks the named files with `--fatal-infos --format=json` under
the analysis options that apply to each one. Naming the files, rather than the
component directory, analyzes exactly the inventory: `analyzer.exclude` cannot
skip a named file, and nothing outside the inventory, such as `.dart_tool`, is
analyzed. The component's pubspec.yaml and every analysis options file are
named as well, because the analyzer reports problems in them, such as an
unknown or deprecated lint, only for named files. Diagnostics of type `LINT`
are lint findings; every other type in those configuration files is a
configuration finding, and elsewhere a type finding, compile errors and the
strict language modes included. Every severity fails.

A diagnostic in a file outside the inventory names that file in its message
and is located at the component's pubspec.yaml.
"""

from __future__ import annotations

import posixpath
from dataclasses import replace
from pathlib import Path

from quality.adapters.dart.config import CONFIG
from quality.adapters.dart.environment import Sdk
from quality.adapters.dart.lockfile import PUBSPEC
from quality.adapters.dart.options import option_files
from quality.adapters.helperio import repository_path
from quality.adapters.native import fail, run_tool
from quality.adapters.target import Entry, Target, document, findings, location, number
from quality.diagnostics import Diagnostic, Location, Rule

LINT = Rule(
    id="dart.lint",
    title="Dart lint finding",
    requirement="R04",
    explanation=(
        "Source and test files are analyzed by `dart analyze --fatal-infos` of the pinned SDK "
        "with the recommended lints and the component's analysis options. Every lint fails."
    ),
    remedy="Fix the finding; do not lower or turn off the lint.",
)
TYPES = Rule(
    id="dart.types",
    title="Dart compile or strict type error",
    requirement="R04",
    explanation=(
        "Source and test files are analyzed by the pinned SDK with strict-casts, "
        "strict-inference, and strict-raw-types. Every error, warning, and hint fails."
    ),
    remedy="Fix the error; do not weaken the analysis options or add ignore comments.",
)
RULES = (LINT, TYPES)

TIMEOUT = 1800.0
TOOL = "dart analyze"
# 0 is clean; 1, 2, and 3 report infos, warnings, and errors.
ACCEPTED = frozenset({0, 1, 2, 3})


def _position(entry: Entry) -> tuple[int | None, int | None]:
    where = entry.get("location")
    span = where.get("range") if isinstance(where, dict) else None
    start = span.get("start") if isinstance(span, dict) else None
    if not isinstance(start, dict):
        return None, None
    return number(start, "line"), number(start, "column")


def _file(entry: Entry) -> str | None:
    where = entry.get("location")
    name = where.get("file") if isinstance(where, dict) else None
    return name if isinstance(name, str) else None


def _outside(target: Target, name: str | None, line: int | None) -> str:
    """Name the position of a diagnostic outside the inventory, for its message."""
    shown = "the analysis" if name is None else repository_path(target.snapshot, name) or name
    return shown if line is None else f"{shown}:{line}"


def configuration(target: Target) -> tuple[str, ...]:
    """Return the component's pubspec.yaml and analysis options files as repository paths."""
    found = (target.directory / PUBSPEC, *option_files(target.directory))
    return tuple(path.relative_to(target.snapshot).as_posix() for path in found)


def _rule(entry: Entry, path: str | None, settings: tuple[str, ...]) -> Rule:
    if entry.get("type") == "LINT":
        return LINT
    return CONFIG if path in settings else TYPES


def _diagnostic(target: Target, settings: tuple[str, ...], entry: Entry) -> Diagnostic:
    code, message = entry.get("code"), entry.get("problemMessage")
    if not isinstance(code, str) or not isinstance(message, str):
        fail(TOOL, "a diagnostic has no code or message")
    text = f"[{code}] {message}"
    name = _file(entry)
    path = None if name is None else repository_path(target.snapshot, name)
    rule = _rule(entry, path, settings)
    line, column = _position(entry)
    if path is None or path not in target.files:
        pubspec = posixpath.normpath(posixpath.join(target.component.root, PUBSPEC))
        return rule.diagnostic(
            f"{_outside(target, name, line)}: {text}",
            location=Location(pubspec),
            component=target.component.id,
        )
    return rule.diagnostic(
        text, location=location(path, line, column), component=target.component.id
    )


def dart_analyze(target: Target, *, sdk: Sdk, home: Path) -> list[Diagnostic]:
    """Report the analyzer's diagnostics for the named files and the configuration."""
    settings = configuration(target)
    named = replace(target, files=(*target.files, *settings))
    argv = sdk.command("analyze", "--fatal-infos", "--format=json", *named.arguments())
    completed = run_tool(
        sdk.run_in(argv, named.directory, home, timeout=TIMEOUT), name=TOOL, accepted=ACCEPTED
    )
    report = document(TOOL, completed)
    if not isinstance(report, dict):
        fail(TOOL, "output is not a JSON object")
    entries = findings(TOOL, report.get("diagnostics"))
    return [_diagnostic(named, settings, entry) for entry in entries]
