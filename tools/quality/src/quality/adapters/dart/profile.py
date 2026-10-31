"""The Dart profile adapter: configuration, `dart format`, `dart analyze`, and package:test.

Every tool runs from the pinned SDK with the packages the component locks,
which `prepare` resolves offline once per lock directory and work area. The
gates run on the Dart files of the inventory, and one failing tool does not
stop the others. The helper's structure answers are shared by `measure` and
the unit runner, which needs them to find the part files.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from quality.adapters import native
from quality.adapters.dart import analyzer, config, formatter
from quality.adapters.dart.analyzer import dart_analyze
from quality.adapters.dart.config import DartConfig, check_config, dart_files
from quality.adapters.dart.environment import Environments, Sdk
from quality.adapters.dart.formatter import dart_format
from quality.adapters.dart.helper import Helper
from quality.adapters.dart.runner import filled, generated_test, run_dart_test, source_files
from quality.adapters.dart.structure import Structures, measurements
from quality.adapters.target import Target
from quality.coverage import CoverageReport, parse_lcov
from quality.diagnostics import Diagnostic, Rule
from quality.metrics import Measurements
from quality.policy import Component
from quality.testing import StaticRequest, Suite, SuiteRequest, SuiteRun

RULES = (*config.RULES, *formatter.RULES, *analyzer.RULES)


def _findings(gate: Callable[[], list[Diagnostic]]) -> list[Diagnostic]:
    try:
        return gate()
    except native.ToolFailedError as error:
        return [error.diagnostic]


@dataclass(slots=True)
class Prepared:
    """A prepared component: its configuration and the SDK that resolved its packages."""

    settings: DartConfig
    sdk: Sdk


@dataclass(slots=True)
class DartProfile:
    """Runs the pinned SDK's tools and the component's locked package:test."""

    environments: Environments
    pins: Mapping[str, str]
    helper: Helper
    structures: Structures = field(init=False)
    _prepared: dict[tuple[Path, str], Prepared] = field(
        default_factory=dict[tuple[Path, str], Prepared]
    )
    _reached: dict[Path, frozenset[str]] = field(default_factory=dict[Path, frozenset[str]])

    def __post_init__(self) -> None:
        self.structures = Structures(self.helper)

    @property
    def rules(self) -> tuple[Rule, ...]:
        """Return the rules the static gates report."""
        return RULES

    def prepare(self, component: Component, snapshot: Path, work: Path) -> list[Diagnostic]:
        """Check the configuration; only an accepted one gets its packages resolved."""
        settings, report = check_config(component, snapshot, self.pins)
        if settings is None or report.found:
            return report.found
        sdk = self.environments.install(settings.lock.directory, work, label=component.id)
        self._prepared[work, component.id] = Prepared(settings, sdk)
        return []

    def _ready(self, component: Component, work: Path) -> Prepared:
        prepared = self._prepared.get((work, component.id))
        if prepared is None:
            native.fail("dart", f"'{component.id}' was not prepared")
        return prepared

    def static(self, request: StaticRequest) -> list[Diagnostic]:
        """Run the formatter and the analyzer on the component's Dart files."""
        prepared = self._ready(request.component, request.work)
        target = Target(request.component, request.snapshot, dart_files(request.files))
        if not target.files:
            return []
        sdk, home = prepared.sdk, request.work / "dart-home" / request.component.id
        found = _findings(lambda: dart_format(target, sdk=sdk, home=home))
        return found + _findings(lambda: dart_analyze(target, sdk=sdk, home=home))

    def measure(self, request: StaticRequest) -> Measurements:
        """Measure the component's Dart files with the helper."""
        prepared = self._ready(request.component, request.work)
        target = Target(request.component, request.snapshot, dart_files(request.files))
        if not target.files:
            return Measurements(())
        files = self.structures.files(
            target, sdk=prepared.sdk, work=request.work, package=prepared.settings.name
        )
        return measurements(files)

    def expected(self, path: str) -> bool:
        """Expect every Dart source file in the coverage report."""
        return PurePosixPath(path).suffix == ".dart"

    def run(self, request: SuiteRequest) -> SuiteRun:
        """Run one suite; a unit suite also imports every library for coverage."""
        prepared = self._ready(request.component, request.work)
        if request.suite is not Suite.UNIT:
            return run_dart_test(prepared.sdk, request, None)
        package = prepared.settings.name
        target = Target(request.component, request.snapshot, source_files(request))
        files = self.structures.files(target, sdk=prepared.sdk, work=request.work, package=package)
        generated = generated_test(request, package, files)
        self._reached[request.output] = generated.reached
        return run_dart_test(prepared.sdk, request, generated)

    def coverage(self, data: bytes, request: SuiteRequest) -> CoverageReport:
        """Parse the LCOV report, then add the compiled files that have nothing to cover."""
        report = parse_lcov(
            data, "lcov.info", root=request.snapshot, cwd=request.directory, branches_measured=True
        )
        return filled(report, self._reached.get(request.output, frozenset()))
