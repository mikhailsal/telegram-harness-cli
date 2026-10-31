"""`quality fix` for Python: Ruff's safe fixes and formatter on working-tree files.

Fixes use the same pinned Ruff, configuration, and explicit file list as the
check, never unsafe fixes, and never stage anything. What remains afterwards is
reported exactly as the check reports it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from quality.adapters.native import run_tool
from quality.adapters.python.config import STATIC_SUFFIXES, Findings
from quality.adapters.python.ruff import TIMEOUT, ruff_format, ruff_lint, ruff_settings
from quality.adapters.target import Target
from quality.diagnostics import Diagnostic
from quality.policy import Component
from quality.process import Command, sanitized

# `ruff check --fix` exits 1 when findings remain; `ruff format` exits 2 on a syntax error.
FIX = ("check", "--fix", "--no-unsafe-fixes", "--quiet")
FORMAT = ("format", "--quiet")


@dataclass(frozen=True, slots=True)
class PythonFixer:
    """Applies the pinned Ruff's safe fixes and formatting to a component's files."""

    ruff: Path

    def _apply(self, target: Target, config: str, argv: tuple[str, ...]) -> None:
        options = ("--config", str(target.directory / config), "--no-force-exclude", "--no-cache")
        command = Command(
            (str(self.ruff), *argv, *options, *target.arguments()),
            cwd=target.directory,
            timeout=TIMEOUT,
            env=sanitized(),
        )
        run_tool(command, name="ruff", accepted={0, 1, 2})

    def fix(self, component: Component, root: Path, files: tuple[str, ...]) -> list[Diagnostic]:
        """Fix and format the files, then return the findings that remain."""
        report = Findings(component)
        config = ruff_settings(root / component.root, root, report.add)
        if report.found:
            return report.found
        checked = tuple(path for path in files if PurePosixPath(path).suffix in STATIC_SUFFIXES)
        if not checked:
            return []
        target = Target(component, root, checked)
        self._apply(target, config, FIX)
        self._apply(target, config, FORMAT)
        return [
            *ruff_format(target, ruff=self.ruff, config=config),
            *ruff_lint(target, ruff=self.ruff, config=config),
        ]
