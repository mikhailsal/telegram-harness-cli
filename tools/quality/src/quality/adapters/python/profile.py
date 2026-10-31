"""The Python profile adapter: configuration, Ruff, Pyright, and pytest with coverage.py."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from quality.adapters import native
from quality.adapters.python import config, pyright, ruff
from quality.adapters.python.config import STATIC_SUFFIXES, NativeConfig, check_config
from quality.adapters.python.environment import Environments, stale_lock
from quality.adapters.python.runner import run_pytest
from quality.adapters.target import Target
from quality.coverage import CoverageReport, parse_coverage_json
from quality.diagnostics import Diagnostic, Rule
from quality.metrics import Measurements
from quality.policy import Component
from quality.testing import StaticRequest, SuiteRequest, SuiteRun

RULES = (*config.RULES, *ruff.RULES, *pyright.RULES)


def _findings(gate: Callable[[], list[Diagnostic]]) -> list[Diagnostic]:
    try:
        return gate()
    except native.ToolFailedError as error:
        return [error.diagnostic]


@dataclass(slots=True)
class PythonProfile:
    """Runs the pinned Ruff and Pyright and the component's locked pytest."""

    ruff: Path
    pyright: Path
    environments: Environments
    _native: dict[Path, NativeConfig] = field(default_factory=dict[Path, NativeConfig])

    @property
    def rules(self) -> tuple[Rule, ...]:
        """Return the rules the static gates report."""
        return RULES

    def prepare(self, component: Component, snapshot: Path, work: Path) -> list[Diagnostic]:
        """Check the configuration; only an accepted one gets an environment."""
        settings, report = check_config(component, snapshot)
        if settings is None or report.found:
            return report.found
        stale = stale_lock(self.environments.uv, snapshot / component.root)
        if stale is not None:
            report.add("uv.lock", f"uv.lock does not match pyproject.toml: {stale}; run `uv lock`")
            return report.found
        self._native[self.environments.location(component, work)] = settings
        self.environments.python(component, snapshot, work)
        return []

    def _settings(self, component: Component, work: Path) -> NativeConfig:
        settings = self._native.get(self.environments.location(component, work))
        if settings is None:
            native.fail("python", f"'{component.id}' was not prepared")
        return settings

    def static(self, request: StaticRequest) -> list[Diagnostic]:
        """Run the formatter, linter, and type checker on the component's Python files."""
        component = request.component
        settings = self._settings(component, request.work)
        checked = tuple(
            path for path in request.files if PurePosixPath(path).suffix in STATIC_SUFFIXES
        )
        if not checked:
            return []
        target = Target(component, request.snapshot, checked)
        python = self.environments.python(component, request.snapshot, request.work)
        found = _findings(lambda: ruff.ruff_format(target, ruff=self.ruff, config=settings.ruff))
        found += _findings(lambda: ruff.ruff_lint(target, ruff=self.ruff, config=settings.ruff))
        found += _findings(
            lambda: pyright.pyright_types(
                target, pyright=self.pyright, config=settings.pyright, python=python
            )
        )
        return found

    def measure(self, request: StaticRequest) -> Measurements | None:
        """Leave Python structure to the common gates, which parse the candidate's bytes."""
        return None

    def expected(self, path: str) -> bool:
        """Expect every Python module in the coverage report; stubs hold no code."""
        return PurePosixPath(path).suffix == ".py"

    def run(self, request: SuiteRequest) -> SuiteRun:
        """Run one suite with the component's locked interpreter."""
        settings = self._settings(request.component, request.work)
        python = self.environments.python(request.component, request.snapshot, request.work)
        return run_pytest(python, request, settings.pytest)

    def coverage(self, data: bytes, request: SuiteRequest) -> CoverageReport:
        """Parse coverage.py JSON, whose paths are relative to the component directory."""
        return parse_coverage_json(
            data, "coverage.json", root=request.snapshot, cwd=request.directory
        )
