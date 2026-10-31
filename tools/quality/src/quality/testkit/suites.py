"""A scripted profile adapter for engine tests: it writes exactly the reports it is told to."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from quality.adapters.native import ToolFailedError
from quality.coverage import CoverageReport, parse_coverage_json
from quality.diagnostics import Diagnostic, Rule
from quality.metrics import Measurements
from quality.policy import Component
from quality.testing import Outcome, StaticRequest, Suite, SuiteRequest, SuiteRun
from quality.testkit.reports import FileSpec, coverage_json, junit_xml

type Cases = tuple[tuple[str, Outcome], ...]
type Hook = Callable[[SuiteRequest, SuiteRun], SuiteRun]
type Containment = Callable[[tuple[str, ...]], tuple[tuple[str, ...], str | None]]


def direct(reason: str | None = None) -> Containment:
    """Return a stand-in for network containment that starts suites as they are.

    `reason` is what the stand-in says about the missing isolation; None claims
    the suite was isolated.
    """

    def start(argv: tuple[str, ...]) -> tuple[tuple[str, ...], str | None]:
        return argv, reason

    return start


@dataclass(frozen=True, slots=True)
class Script:
    """What one suite run reports; `files` None writes no coverage report.

    Coverage paths are relative to the component root, where native tools run.
    """

    cases: Cases = (("test_ok", Outcome.PASSED),)
    returncode: int = 0
    files: Mapping[str, FileSpec] | None = None
    branch: bool = True


FULL = FileSpec.first(2, 2, (1, 1))


def covering(request: SuiteRequest) -> Script:
    """Script a run that fully covers every Python file under the component's source roots."""
    files = {
        path.relative_to(request.directory).as_posix(): FULL
        for root in request.component.source
        for path in sorted((request.directory / root).rglob("*.py"))
    }
    return Script(files=files)


def _no_findings() -> Mapping[str, list[Diagnostic]]:
    return {}


@dataclass(slots=True)
class ScriptedRunner:
    """Runs no tools; writes each script's reports and records every request.

    `hook` can replace the run after the reports are written, to simulate a
    misbehaving runner. Without a script, a run passes and, with `cover`, fully
    covers the component's sources. `problems` are what `prepare` reports per
    component, `broken` makes it raise that diagnostic as a tool failure,
    `findings` are what `static` reports, and `measurements` are what `measure`
    returns; a component without them is left to the common gates.
    """

    scripts: Mapping[tuple[str, Suite], Script]
    hook: Hook | None = None
    cover: bool = False
    rules: tuple[Rule, ...] = ()
    problems: Mapping[str, list[Diagnostic]] = field(default_factory=_no_findings)
    broken: Mapping[str, Diagnostic] = field(default_factory=dict[str, Diagnostic])
    findings: Mapping[str, list[Diagnostic]] = field(default_factory=_no_findings)
    measurements: Mapping[str, Measurements] = field(default_factory=dict[str, Measurements])
    requests: list[SuiteRequest] = field(default_factory=list[SuiteRequest])
    parsed: list[tuple[str, Suite]] = field(default_factory=list[tuple[str, Suite]])
    fresh: list[bool] = field(default_factory=list[bool])
    prepared: list[str] = field(default_factory=list[str])
    checked: list[tuple[str, tuple[str, ...]]] = field(
        default_factory=list[tuple[str, tuple[str, ...]]]
    )
    statics: list[StaticRequest] = field(default_factory=list[StaticRequest])

    def prepare(self, component: Component, snapshot: Path, work: Path) -> list[Diagnostic]:
        """Report the scripted configuration problems, or fail as scripted."""
        self.prepared.append(component.id)
        if component.id in self.broken:
            raise ToolFailedError(self.broken[component.id])
        return list(self.problems.get(component.id, []))

    def static(self, request: StaticRequest) -> list[Diagnostic]:
        """Return the scripted findings and record which files were named."""
        self.statics.append(request)
        self.checked.append((request.component.id, request.files))
        return list(self.findings.get(request.component.id, []))

    def measure(self, request: StaticRequest) -> Measurements | None:
        """Return the scripted measurements of the component, if any."""
        return self.measurements.get(request.component.id)

    def expected(self, path: str) -> bool:
        """Expect Python source files."""
        return path.endswith(".py")

    def _script(self, request: SuiteRequest) -> Script:
        script = self.scripts.get((request.component.id, request.suite))
        if script is not None:
            return script
        return covering(request) if self.cover and request.suite is Suite.UNIT else Script()

    def run(self, request: SuiteRequest) -> SuiteRun:
        """Write the scripted reports into the request's output directory."""
        self.requests.append(request)
        self.fresh.append(not any(request.output.iterdir()))
        script = self._script(request)
        results = request.output / "junit.xml"
        results.write_bytes(junit_xml(script.cases))
        run = SuiteRun(script.returncode, results)
        if script.files is not None:
            report = request.output / "coverage.json"
            report.write_bytes(coverage_json(script.files, branch=script.branch))
            run = SuiteRun(script.returncode, results, report)
        return self.hook(request, run) if self.hook is not None else run

    def coverage(self, data: bytes, request: SuiteRequest) -> CoverageReport:
        """Parse the coverage.py JSON report relative to the component directory."""
        self.parsed.append((request.component.id, request.suite))
        return parse_coverage_json(
            data, "coverage.json", root=request.snapshot, cwd=request.directory
        )
