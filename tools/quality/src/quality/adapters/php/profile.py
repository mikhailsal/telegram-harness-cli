"""The PHP profile adapter: Composer, PHP CS Fixer, PHPStan, and PHPUnit with Xdebug.

Every tool is the component's own locked package, run by the system PHP with
the gate's settings, from the `vendor/` that `prepare` installs offline into
the snapshot once per component and work area. A snapshot that already holds
the component's `vendor/` committed it, which is a configuration finding. The
gates run on the PHP files of the inventory, and one failing tool does not
stop the other. The helper's structure answers are shared by `measure` and the
unit runner, which loads the declaring files and checks that Xdebug measured
the branches of every file with functions (decision 0013).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from quality.adapters import native
from quality.adapters.php import analyzer, config, formatter, gate
from quality.adapters.php.analyzer import phpstan
from quality.adapters.php.composer import Environments
from quality.adapters.php.config import VENDOR, PhpConfig, check_config, php_files
from quality.adapters.php.formatter import cs_check
from quality.adapters.php.helper import Helper
from quality.adapters.php.runner import (
    JSONL,
    JSONL_FILES,
    run_phpunit,
    source_files,
    unmeasured,
)
from quality.adapters.php.runtime import Php
from quality.adapters.php.structure import PhpFile, Structures, measurements
from quality.adapters.target import Target
from quality.boundedio import read_bounded
from quality.coverage import CoverageReport, JsonlReport, parse_phpunit
from quality.diagnostics import Diagnostic, Rule
from quality.metrics import Measurements
from quality.policy import Component
from quality.testing import StaticRequest, Suite, SuiteRequest, SuiteRun

RULES = (*config.RULES, *formatter.RULES, *analyzer.RULES)
COMMITTED = "`vendor/` is committed; Composer installs it from composer.lock, so remove it"


def _findings(gate_run: Callable[[], list[Diagnostic]]) -> list[Diagnostic]:
    try:
        return gate_run()
    except native.ToolFailedError as error:
        return [error.diagnostic]


@dataclass(slots=True)
class Prepared:
    """A prepared component: its configuration and the PHP that installed its packages."""

    settings: PhpConfig
    php: Php


@dataclass(slots=True)
class PhpProfile:
    """Runs the component's locked PHP tools with the gate's settings."""

    environments: Environments
    pins: Mapping[str, str]
    helper: Helper
    structures: Structures = field(init=False)
    _prepared: dict[tuple[Path, str], Prepared] = field(
        default_factory=dict[tuple[Path, str], Prepared]
    )
    _units: dict[Path, dict[str, int]] = field(default_factory=dict[Path, dict[str, int]])

    def __post_init__(self) -> None:
        self.structures = Structures(self.helper)

    @property
    def rules(self) -> tuple[Rule, ...]:
        """Return the rules the static gates report."""
        return RULES

    def prepare(self, component: Component, snapshot: Path, work: Path) -> list[Diagnostic]:
        """Check the configuration; only an accepted one gets its packages installed."""
        vendor = snapshot / component.root / VENDOR
        committed = vendor.is_symlink() or vendor.exists()
        settings, report = check_config(component, snapshot, self.pins)
        if committed:
            report.add(VENDOR, COMMITTED)
        if settings is None or report.found:
            return report.found
        directory = settings.lock.directory
        php = self.environments.install(directory, work, label=component.id, add=report.add)
        if report.found:
            return report.found
        self._prepared[work, component.id] = Prepared(settings, php)
        return []

    def _ready(self, component: Component, work: Path) -> Prepared:
        prepared = self._prepared.get((work, component.id))
        if prepared is None:
            native.fail("php", f"'{component.id}' was not prepared")
        return prepared

    def _files(self, prepared: Prepared, target: Target, work: Path) -> list[PhpFile]:
        return self.structures.files(
            target, php=prepared.php, work=work, prefixes=prepared.settings.autoload
        )

    def static(self, request: StaticRequest) -> list[Diagnostic]:
        """Run the formatter and the analyzer on the component's PHP files."""
        prepared = self._ready(request.component, request.work)
        target = Target(request.component, request.snapshot, php_files(request.files))
        if not target.files:
            return []
        php, home = prepared.php, request.work / "php-home" / request.component.id
        formatting, analysis = gate.settings(request.work)
        found = _findings(lambda: cs_check(target, php=php, settings=formatting, home=home))
        return found + _findings(lambda: phpstan(target, php=php, settings=analysis, home=home))

    def measure(self, request: StaticRequest) -> Measurements:
        """Measure the component's PHP files with the helper."""
        prepared = self._ready(request.component, request.work)
        target = Target(request.component, request.snapshot, php_files(request.files))
        if not target.files:
            return Measurements(())
        return measurements(self._files(prepared, target, request.work))

    def expected(self, path: str) -> bool:
        """Expect every PHP source file in the coverage report."""
        return PurePosixPath(path).suffix == ".php"

    def run(self, request: SuiteRequest) -> SuiteRun:
        """Run one suite; a unit suite also loads every declaring file for coverage."""
        prepared = self._ready(request.component, request.work)
        if request.suite is not Suite.UNIT:
            return run_phpunit(prepared.php, request, ())
        target = Target(request.component, request.snapshot, source_files(request))
        files = self._files(prepared, target, request.work) if target.files else []
        self._units[request.output] = {file.path: file.units for file in files}
        return run_phpunit(prepared.php, request, files)

    def coverage(self, data: bytes, request: SuiteRequest) -> CoverageReport:
        """Combine Clover and JSONL; a file with functions but no branch is unmeasured."""
        directory = request.output / JSONL
        jsonl = JsonlReport(*(read_bounded(directory / name) for name in JSONL_FILES))
        report = parse_phpunit(data, jsonl, root=request.snapshot, cwd=request.directory)
        return unmeasured(report, self._units.get(request.output, {}))
