"""The isolated production build of a browser component (decision 0018).

The gate builds the candidate snapshot, never the work tree, with the
component's locked Vite in production mode and the configuration of
`buildconfig`. The process environment is the sanitized one, so no `GIT_*` or
other ambient variable reaches it, plus only the declared public values. A
build that fails is a `typescript.build` finding. What the boundary plugin
recorded becomes `typescript.boundaries` findings, except for imports by the
browser source files, which the static check reports, and by the server
modules themselves. The output is then hashed and validated by `bundle`.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from quality.adapters.helperio import Entry, Fields, repository_path
from quality.adapters.native import EXCERPT, fail, failure
from quality.adapters.typescript import buildconfig, bundle
from quality.adapters.typescript.boundaries import BOUNDARIES
from quality.adapters.typescript.browser import server_module
from quality.adapters.typescript.environment import Node
from quality.boundedio import ReportError, load_json
from quality.diagnostics import Diagnostic, Location, Rule
from quality.policy import Component
from quality.process import Command, ProcessError, run

BUILD = Rule(
    id="typescript.build",
    title="The production build of a browser component failed",
    requirement="R04",
    explanation=(
        "The gate builds the candidate with the component's locked Vite in production mode, "
        "with no `.env` files, only the declared public values, and a new output directory. "
        "A build that fails cannot be served or tested."
    ),
    remedy="Run the build locally with `vite build` and fix the error it reports.",
)
RULES = (BUILD,)
TOOL = "vite"
BUILD_TIMEOUT = 1800.0
FIELDS = Fields(TOOL)
MESSAGES = {
    "builtin": "the production build imports the Node builtin '{name}'",
    "module": "the production build includes the server-only module {name}",
    "package": "the production build includes the server-only package '{name}'",
}


@dataclass(frozen=True, slots=True)
class BuildRequest:
    """One build: the component, its snapshot, a new directory of the run, and its files.

    `files` are the repository paths the component owns, `sources` the browser
    source files the static check covers, and `identity` names the candidate.
    """

    component: Component
    snapshot: Path
    run: Path
    files: tuple[str, ...]
    sources: frozenset[str]
    identity: str

    @property
    def directory(self) -> Path:
        """The component directory in the snapshot, where Vite runs."""
        return self.snapshot / self.component.root


@dataclass(frozen=True, slots=True)
class BuildOutcome:
    """The findings of one build, and its output and resolved `base` when it succeeded."""

    diagnostics: list[Diagnostic]
    output: bundle.Output | None = None
    base: str = "/"


def _place(snapshot: Path, name: str) -> str:
    return repository_path(snapshot, name) or name


def _violations(request: BuildRequest, report: Entry) -> Iterator[Diagnostic]:
    component, snapshot = request.component, request.snapshot
    for entry in FIELDS.entries(report, "violations"):
        kind, name = FIELDS.text(entry, "kind"), FIELDS.text(entry, "name")
        if kind not in MESSAGES:
            fail(TOOL, f"{buildconfig.REPORT}: unknown violation kind '{kind}'")
        importer = _place(snapshot, FIELDS.text(entry, "importer"))
        if importer in request.sources or server_module(component, importer):
            continue
        message = MESSAGES[kind].format(name=_place(snapshot, name) if kind == "module" else name)
        yield BOUNDARIES.diagnostic(
            f"{message}, imported by {importer}",
            location=Location(importer),
            component=component.id,
        )


def _report(path: Path) -> Entry:
    try:
        report = load_json(path)
    except ReportError as error:
        raise failure(TOOL, f"the build wrote no usable boundary report: {error}") from error
    if not isinstance(report, dict):
        fail(TOOL, f"{buildconfig.REPORT} must hold a JSON object")
    return report


def build_command(node: Node, script: Path, request: BuildRequest) -> Command:
    """Write the run's configuration and return the command that builds with it."""
    component = request.component
    home = request.run / "home"
    temporary, cache = home / "tmp", home / ".cache"
    for directory in (request.run / "env", temporary, cache):
        directory.mkdir(parents=True)
    servers = [
        (request.snapshot / path).resolve()
        for path in request.files
        if server_module(component, path)
    ]
    config = request.run / buildconfig.WRAPPER
    text = buildconfig.wrapper(
        request.directory,
        request.run,
        servers=servers,
        packages=component.browser_settings.server_packages,
    )
    config.write_text(text, encoding="utf-8")
    extra = {
        "TMPDIR": str(temporary),
        "XDG_CACHE_HOME": str(cache),
        "CI": "1",
        **component.browser_settings.public_env,
    }
    argv = (str(node.executable), str(script), "build", "--config", str(config))
    return Command(
        (*argv, "--mode", "production", "--logLevel", "warn"),
        cwd=request.directory,
        timeout=BUILD_TIMEOUT,
        env=node.environ(home, extra),
    )


def production_build(node: Node, script: Path, request: BuildRequest) -> BuildOutcome:
    """Build the component, report its boundary crossings, and validate its output."""
    component = request.component
    try:
        completed = run(build_command(node, script, request))
    except ProcessError as error:
        raise failure(TOOL, str(error)) from error
    if not completed.ok:
        detail = (completed.stderr_text() or completed.stdout_text()).strip()[:EXCERPT]
        message = f"vite build exited with status {completed.returncode}: {detail}"
        return BuildOutcome([BUILD.diagnostic(message, component=component.id)])
    report = _report(request.run / buildconfig.REPORT)
    found = list(_violations(request, report))
    output = bundle.collect(request.run / "dist", request.identity)
    base = FIELDS.text(report, "base")
    found += bundle.check_output(component, output, base)
    return BuildOutcome(found, output, base)
