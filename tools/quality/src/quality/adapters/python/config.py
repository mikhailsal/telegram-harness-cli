"""The native configuration of a Python component, checked before its tools run.

Ruff, Pyright, and pytest read settings from files the candidate controls. The
profile names those files explicitly, so nothing outside the snapshot is
discovered, and it rejects settings that weaken the profile or resolve code from
outside the snapshot. Coverage settings are never read: the runner writes its own
(decision 0009).
"""

from __future__ import annotations

import configparser
import posixpath
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from quality.adapters.python.pyright import pyright_settings
from quality.adapters.python.ruff import ruff_settings
from quality.adapters.settings import (
    CONFIG_LIMIT,
    Table,
    inside,
    load_toml,
    strings,
    table,
)
from quality.boundedio import ReportError, read_bounded
from quality.diagnostics import Diagnostic, Location, Rule
from quality.policy import Component

# The files Ruff and Pyright check: modules and stubs.
STATIC_SUFFIXES = frozenset({".py", ".pyi"})
CONFIG = Rule(
    id="python.config",
    title="Python tool configuration is missing, weakens the profile, or leaves the snapshot",
    requirement="R04",
    explanation=(
        "A Python component needs pyproject.toml with a [project] table, a uv.lock that matches "
        "it and locks pytest, pytest-cov, and coverage, an exact .python-version, an explicit "
        "Ruff lint selection, and Pyright in strict mode without ignored paths or weakened "
        "rules. Paths in tool settings must stay inside the repository."
    ),
    remedy="Fix the named setting in the component's configuration file.",
)
RULES = (CONFIG,)

TEST_PACKAGES = ("coverage", "pytest", "pytest-cov")
# Lockfile sources whose value is always a path relative to the component.
LOCAL_SOURCES = frozenset({"path", "directory", "editable", "virtual"})
VERSION = re.compile(r"\d+\.\d+\.\d+")
PYTEST_FILES = ("pytest.ini", ".pytest.ini")


@dataclass(frozen=True, slots=True)
class NativeConfig:
    """The file each tool is pointed at, relative to the component directory."""

    ruff: str
    pyright: str
    pytest: str


@dataclass(slots=True)
class Findings:
    """Problems found in one component's configuration files."""

    component: Component
    found: list[Diagnostic] = field(default_factory=list[Diagnostic])

    def add(self, file: str, message: str) -> None:
        """Record a problem in a file relative to the component directory."""
        location = Location(posixpath.normpath(posixpath.join(self.component.root, file)))
        self.found.append(
            CONFIG.diagnostic(message, location=location, component=self.component.id)
        )


def _local_path(kind: str, value: str) -> str | None:
    """Return the local path a lockfile source names, or None for a remote source."""
    if kind in LOCAL_SOURCES:
        return value
    if value.startswith("file://"):
        return value.removeprefix("file://")
    return None if "://" in value else value


def _sources(packages: list[Table], directory: Path, snapshot: Path, report: Findings) -> None:
    for package in packages:
        for kind, value in (table(package, "source") or {}).items():
            path = _local_path(kind, value) if isinstance(value, str) else None
            if path is not None and not inside(directory, path, snapshot):
                name = package.get("name")
                report.add("uv.lock", f"{name!r} is locked from {value!r}, outside the repository")


def _lock(directory: Path, snapshot: Path, report: Findings) -> None:
    try:
        lock = load_toml(directory / "uv.lock")
    except ReportError as error:
        report.add("uv.lock", f"the lockfile is missing or unreadable ({error}); run `uv lock`")
        return
    listed = lock.get("package")
    entries = cast(list[object], listed) if isinstance(listed, list) else []
    packages = [cast(Table, entry) for entry in entries if isinstance(entry, Mapping)]
    names = {str(package.get("name", "")).lower().replace("_", "-") for package in packages}
    for name in TEST_PACKAGES:
        if name not in names:
            report.add("uv.lock", f"the lockfile does not lock {name}; add it to the dev group")
    _sources(packages, directory, snapshot, report)


def _python_version(directory: Path, report: Findings) -> None:
    try:
        text = read_bounded(directory / ".python-version", CONFIG_LIMIT).decode("utf-8", "replace")
    except ReportError:
        report.add(".python-version", "pin the interpreter with an exact version, e.g. 3.14.8")
        return
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) != 1 or not VERSION.fullmatch(lines[0]):
        report.add(".python-version", "the file must hold one exact version such as 3.14.8")


def pytest_file(directory: Path) -> str:
    """Return the file pytest is pointed at, relative to the component directory."""
    return next((name for name in PYTEST_FILES if (directory / name).exists()), "pyproject.toml")


def _pytest_paths(directory: Path, name: str, pyproject: Table) -> list[str]:
    if name in PYTEST_FILES:
        parser = configparser.ConfigParser(interpolation=None)
        text = read_bounded(directory / name, CONFIG_LIMIT).decode("utf-8", "replace")
        parser.read_string(text, source=name)
        return parser.get("pytest", "pythonpath", fallback="").split()
    # pytest reads `[tool.pytest.ini_options]` or, natively, `[tool.pytest]`; check both.
    paths: list[str] = []
    for options in (
        table(pyproject, "tool", "pytest", "ini_options"),
        table(pyproject, "tool", "pytest"),
    ):
        value = (options or {}).get("pythonpath", [])
        paths.extend(value.split() if isinstance(value, str) else strings(value))
    return paths


def _pytest(directory: Path, snapshot: Path, pyproject: Table, report: Findings) -> str:
    name = pytest_file(directory)
    try:
        paths = _pytest_paths(directory, name, pyproject)
    except (ReportError, configparser.Error) as error:
        report.add(name, f"the pytest configuration is unusable: {error}")
        return name
    for value in paths:
        if not inside(directory, value, snapshot):
            report.add(name, f"pytest 'pythonpath' entry {value!r} points outside the repository")
    return name


def check_config(component: Component, snapshot: Path) -> tuple[NativeConfig | None, Findings]:
    """Check a component's configuration; without a usable pyproject.toml no tool can run."""
    directory = snapshot / component.root
    report = Findings(component)
    try:
        pyproject = load_toml(directory / "pyproject.toml")
    except ReportError as error:
        report.add(
            "pyproject.toml", f"a pyproject.toml with a [project] table is required ({error})"
        )
        return None, report
    if table(pyproject, "project") is None:
        report.add("pyproject.toml", "pyproject.toml needs a [project] table for uv")
        return None, report
    _lock(directory, snapshot, report)
    _python_version(directory, report)
    ruff = ruff_settings(directory, snapshot, report.add)
    pyright = pyright_settings(directory, snapshot, report.add)
    pytest = _pytest(directory, snapshot, pyproject, report)
    return NativeConfig(ruff=ruff, pyright=pyright, pytest=pytest), report
