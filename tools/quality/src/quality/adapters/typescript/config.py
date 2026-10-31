"""The native configuration of a TypeScript component, checked before its tools run.

Prettier, ESLint, the compiler, and Vitest read their settings from files the
candidate controls, and they import the component's own packages. The profile
requires the files that make those runs reproducible: package.json, a lockfile
that locks the pinned tools, tsconfig.json, and an ESLint flat configuration at
the component root. Legacy `.eslintrc*` files are rejected because ESLint 10
ignores them, so they could only mislead a reader (decision 0011).

JavaScript among the source and test files is reported with the static gates,
which receive the inventory's file list; the check fails either way.
"""

from __future__ import annotations

import posixpath
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from quality.adapters.settings import load_json_object
from quality.adapters.typescript.lockfile import MANIFEST, Lock, check_lock
from quality.adapters.typescript.manifests import LOCKFILE, mismatches
from quality.boundedio import ReportError
from quality.diagnostics import Diagnostic, Location, Rule
from quality.policy import Component

CONFIG = Rule(
    id="typescript.config",
    title="TypeScript tool configuration is missing, unpinned, or leaves the snapshot",
    requirement="R04",
    explanation=(
        "A TypeScript component needs package.json, a package-lock.json that locks the pinned "
        "TypeScript, ESLint, typescript-eslint, Prettier, Vitest, and V8 coverage packages, "
        "tsconfig.json, and an ESLint flat configuration at its root; a browser component's "
        "lockfile also locks the pinned React, Vite, testing, lint, and Playwright packages. "
        "Its source and test files are TypeScript, and its lockfile installs nothing from "
        "outside the repository."
    ),
    remedy=(
        "Fix the named file or convert it to TypeScript; `npm install --save-exact` writes "
        "the pinned versions."
    ),
)
RULES = (CONFIG, LOCKFILE)

TSCONFIG = "tsconfig.json"
ESLINT_CONFIGS = tuple(f"eslint.config.{ext}" for ext in ("js", "mjs", "cjs", "ts", "mts", "cts"))
LEGACY_ESLINT = ".eslintrc"
TYPESCRIPT_SUFFIXES = frozenset({".ts", ".tsx", ".mts", ".cts"})
JAVASCRIPT_SUFFIXES = frozenset({".js", ".jsx", ".mjs", ".cjs"})


def typescript_files(files: tuple[str, ...]) -> tuple[str, ...]:
    """Return the TypeScript files of a list, declaration files included."""
    return tuple(path for path in files if PurePosixPath(path).suffix in TYPESCRIPT_SUFFIXES)


@dataclass(frozen=True, slots=True)
class NativeConfig:
    """The lockfile a component installs from; its other files are at its root."""

    lock: Lock


@dataclass(slots=True)
class Findings:
    """Problems found in one component's configuration files."""

    component: Component
    found: list[Diagnostic] = field(default_factory=list[Diagnostic])

    def add(self, file: str, message: str, *, rule: Rule = CONFIG) -> None:
        """Record a problem in a file relative to the component directory."""
        location = Location(posixpath.normpath(posixpath.join(self.component.root, file)))
        self.found.append(rule.diagnostic(message, location=location, component=self.component.id))


def _eslint(directory: Path, report: Findings) -> None:
    if not any((directory / name).is_file() for name in ESLINT_CONFIGS):
        report.add(ESLINT_CONFIGS[0], "an ESLint flat configuration (eslint.config.*) is required")
    for path in sorted(directory.glob(f"{LEGACY_ESLINT}*")):
        report.add(path.name, "ESLint 10 ignores legacy configuration; move it to eslint.config.*")


def check_config(
    component: Component, snapshot: Path, pins: Mapping[str, str]
) -> tuple[NativeConfig | None, Findings]:
    """Check a component's configuration; without a usable lockfile nothing can run."""
    directory = snapshot / component.root
    report = Findings(component)
    try:
        load_json_object(directory / MANIFEST)
    except ReportError as error:
        report.add(MANIFEST, f"a package.json holding a JSON object is required ({error})")
    if not (directory / TSCONFIG).is_file():
        report.add(TSCONFIG, "a tsconfig.json at the component root is required")
    _eslint(directory, report)
    lock = check_lock(directory, snapshot, pins, report.add, browser=component.in_browser)
    if lock is not None:
        for path, message in mismatches(lock, snapshot):
            name = posixpath.relpath(path.as_posix(), directory.as_posix())
            report.add(name, message, rule=LOCKFILE)
    return (NativeConfig(lock) if lock is not None else None), report


def javascript(component: Component, files: tuple[str, ...]) -> list[Diagnostic]:
    """Report JavaScript among the source and test files; the profile checks TypeScript only."""
    return [
        CONFIG.diagnostic(
            "JavaScript is not type-checked or measured by the TypeScript profile; "
            "convert the file to TypeScript",
            location=Location(path),
            component=component.id,
        )
        for path in files
        if PurePosixPath(path).suffix in JAVASCRIPT_SUFFIXES
    ]
