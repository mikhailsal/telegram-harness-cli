"""The TypeScript profile adapter: configuration, Prettier, ESLint, the compiler, and Vitest.

Every tool runs on the pinned Node with the packages the component locks, which
`prepare` installs offline once per lockfile directory and work area. The gates
run on the TypeScript files of the inventory; Prettier also checks the
component's configuration scripts. A browser component gets the browser gates
as well (decision 0017): its CSS and HTML entry points are formatted, its
ESLint configuration must enable the browser rules, and its imports,
stylesheets, and entry points are checked. One failing tool does not stop the
others.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from quality.adapters import native
from quality.adapters.target import Target
from quality.adapters.typescript import (
    boundaries,
    build,
    bundle,
    chromium,
    config,
    endtoend,
    entries,
    eslint,
    playwright,
    prettier,
    scans,
    stylelint,
    traces,
    typecheck,
)
from quality.adapters.typescript.browser import BrowserFiles, browser_files
from quality.adapters.typescript.chromium import Chromium
from quality.adapters.typescript.config import NativeConfig, check_config, typescript_files
from quality.adapters.typescript.endtoend import BrowserTools, locked_axe, run_browser_suite
from quality.adapters.typescript.environment import Environments, Node, tool_script
from quality.adapters.typescript.helper import HELPER
from quality.adapters.typescript.runner import expected, run_vitest
from quality.adapters.typescript.structure import measure_files
from quality.boundedio import ReportError
from quality.coverage import CoverageReport, parse_lcov
from quality.diagnostics import Diagnostic, Rule
from quality.metrics import Measurements
from quality.policy import Component, Suite
from quality.testing import StaticRequest, SuiteRequest, SuiteRun

PLAYWRIGHT = "@playwright/test"
RULES = (
    *config.RULES,
    *prettier.RULES,
    *eslint.RULES,
    *typecheck.RULES,
    *boundaries.RULES,
    *stylelint.RULES,
    *entries.RULES,
    *build.RULES,
    *bundle.RULES,
    *chromium.RULES,
    *traces.RULES,
    *scans.RULES,
    *playwright.RULES,
    *endtoend.RULES,
)


def _findings(gate: Callable[[], list[Diagnostic]]) -> list[Diagnostic]:
    try:
        return gate()
    except native.ToolFailedError as error:
        return [error.diagnostic]


@dataclass(slots=True)
class Prepared:
    """A prepared component: its configuration and the Node that installed its packages."""

    settings: NativeConfig
    node: Node


@dataclass(slots=True)
class TypeScriptProfile:
    """Runs the component's locked TypeScript tools on the pinned Node."""

    environments: Environments
    pins: Mapping[str, str]
    helper: Path = HELPER
    chromium: Chromium | None = None
    _prepared: dict[tuple[Path, str], Prepared] = field(
        default_factory=dict[tuple[Path, str], Prepared]
    )

    @property
    def rules(self) -> tuple[Rule, ...]:
        """Return the rules the static gates report."""
        return RULES

    def prepare(self, component: Component, snapshot: Path, work: Path) -> list[Diagnostic]:
        """Check the configuration; only an accepted one gets its packages installed."""
        settings, report = check_config(component, snapshot, self.pins)
        if settings is None or report.found:
            return report.found
        node = self.environments.install(settings.lock.directory, work, label=component.id)
        self._prepared[work, component.id] = Prepared(settings, node)
        return []

    def _ready(self, component: Component, work: Path) -> Prepared:
        prepared = self._prepared.get((work, component.id))
        if prepared is None:
            native.fail("typescript", f"'{component.id}' was not prepared")
        return prepared

    def _script(
        self, name: str, prepared: Prepared, directory: Path, command: str | None = None
    ) -> Path:
        top = prepared.settings.lock.directory
        return tool_script(name, directory, top, self.pins[name], command)

    def static(self, request: StaticRequest) -> list[Diagnostic]:
        """Run the formatter, linter, and type checker, and a browser component's own gates."""
        component, work = request.component, request.work
        prepared = self._ready(component, work)
        web = browser_files(request) if component.in_browser else None
        found = config.javascript(component, request.files)
        checked = Target(component, request.snapshot, typescript_files(request.files))
        pages = () if web is None else (*web.styles, *(p for p in web.entries if p in web.owned))
        formatted = Target(component, request.snapshot, (*checked.files, *request.configs, *pages))
        node, home = prepared.node, work / "typescript-home" / component.id

        def formatting() -> list[Diagnostic]:
            script = self._script("prettier", prepared, request.directory)
            return prettier.prettier_format(formatted, node=node, script=script, home=home)

        def linting() -> list[Diagnostic]:
            problems = eslint.eslint_configuration(
                checked,
                node=node,
                work=work,
                program=self.helper,
                required=None if web is None else web.required_rules(),
            )
            script = self._script("eslint", prepared, request.directory)
            return problems + eslint.eslint_lint(checked, node=node, script=script, work=home)

        if formatted.files:
            found += _findings(formatting)
        if checked.files:
            found += _findings(linting)
            found += _findings(
                lambda: typecheck.typescript_types(
                    checked, node=node, work=work, program=self.helper
                )
            )
        if web is not None:
            found += self._browser(request, prepared, web)
        return found

    def _browser(
        self, request: StaticRequest, prepared: Prepared, web: BrowserFiles
    ) -> list[Diagnostic]:
        component, snapshot, work = request.component, request.snapshot, request.work
        node = prepared.node
        found = stylelint.unsupported_variants(component, web.variants)
        found += entries.check_entries(component, snapshot, web.entries, web.owned)

        def styles() -> list[Diagnostic]:
            script = self._script("stylelint", prepared, request.directory)
            styled = Target(component, snapshot, web.styles)
            home = work / "typescript-home" / component.id
            return stylelint.stylelint_styles(styled, node=node, script=script, work=home)

        if web.sources:
            sources = Target(component, snapshot, web.sources)
            found += _findings(
                lambda: boundaries.import_boundaries(
                    sources, node=node, work=work, program=self.helper
                )
            )
        if web.styles:
            found += _findings(styles)
        return found

    def measure(self, request: StaticRequest) -> Measurements:
        """Measure the component's TypeScript files with the helper."""
        prepared = self._ready(request.component, request.work)
        files = typescript_files(request.files)
        if not files:
            return Measurements(())
        target = Target(request.component, request.snapshot, files)
        return measure_files(target, node=prepared.node, work=request.work, program=self.helper)

    def expected(self, path: str) -> bool:
        """Expect every TypeScript source file in the coverage report except declarations."""
        return expected(path)

    def run(self, request: SuiteRequest) -> SuiteRun:
        """Run one suite with the component's locked Vitest, or a browser one in Chromium.

        End-to-end suites of a browser component run with Playwright Test
        against the gate's server of its production build (decision 0018).
        """
        prepared = self._ready(request.component, request.work)
        if request.component.in_browser and request.suite is Suite.E2E:
            return run_browser_suite(self._browser_tools(prepared, request), request)
        script = self._script("vitest", prepared, request.directory)
        return run_vitest(prepared.node, script, request)

    def _browser_tools(self, prepared: Prepared, request: SuiteRequest) -> BrowserTools:
        component, lock, directory = request.component, prepared.settings.lock, request.directory
        if self.chromium is None:
            native.fail(chromium.TOOL, "this build of the gate has no browser")
        try:
            expected = chromium.expected_version(lock, directory, lock.directory)
        except ReportError as error:
            message = f"cannot tell which Chromium the locked Playwright expects: {error}"
            raise native.ToolFailedError(
                chromium.VERSION.diagnostic(message, component=component.id)
            ) from error
        return BrowserTools(
            node=prepared.node,
            vite=self._script("vite", prepared, directory),
            playwright=self._script(PLAYWRIGHT, prepared, directory, playwright.COMMAND),
            browser=self.chromium.executable(component.id, expected),
            axe=locked_axe(directory, lock.directory),
        )

    def coverage(self, data: bytes, request: SuiteRequest) -> CoverageReport:
        """Parse the LCOV report, whose paths are relative to the component directory."""
        return parse_lcov(
            data, "lcov.info", root=request.snapshot, cwd=request.directory, branches_measured=True
        )
