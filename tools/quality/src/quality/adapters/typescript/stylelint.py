"""Stylelint, the stylesheet linter of a browser component (decision 0017).

The pinned Stylelint checks the component's CSS files with a configuration the
gate writes into the work area: it extends the standard and CSS Modules
configurations and then the component's own configuration file, if there is
one, so the component can refine the rules but cannot lose the baseline by
leaving it out. Ignore files do not apply, a file the configuration ignores
fails, and so does a needless or invalid-scope disable comment. Every warning
is an error. A Sass, Less, or Stylus file is an unsupported variant.
"""

from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath

from quality.adapters.native import fail, run_tool
from quality.adapters.settings import load_json_object
from quality.adapters.target import Entry, Target, findings, location, number
from quality.adapters.typescript.browser import VARIANTS
from quality.adapters.typescript.environment import Node
from quality.boundedio import ReportError, load_json
from quality.diagnostics import Diagnostic, Location, Rule
from quality.policy import Component
from quality.process import Command

STYLES = Rule(
    id="typescript.styles",
    title="Stylesheet finding",
    requirement="R04",
    explanation=(
        "The CSS files of a browser component are linted by the pinned Stylelint with "
        "stylelint-config-standard and stylelint-config-css-modules, followed by the "
        "component's own Stylelint configuration file. Ignore files and warning levels do "
        "not apply, and needless or misplaced disable comments fail. Sass, Less, and Stylus "
        "are not supported."
    ),
    remedy=(
        "Fix the stylesheet; write CSS or CSS Modules instead of a preprocessor language, "
        "and keep the Stylelint configuration in a .stylelintrc.* or stylelint.config.* file."
    ),
)
RULES = (STYLES,)

BASELINE = ("stylelint-config-standard", "stylelint-config-css-modules")
# Stylelint's own search order, without the `stylelint` field of package.json.
CONFIGS = (
    ".stylelintrc",
    *(f".stylelintrc.{suffix}" for suffix in ("cjs", "mjs", "js", "ts", "json", "yaml", "yml")),
    *(f"stylelint.config.{suffix}" for suffix in ("cjs", "mjs", "js", "ts")),
)
OPTIONS = (
    "--formatter",
    "json",
    "--report-needless-disables",
    "--report-invalid-scope-disables",
    "--ignore-path",
    os.devnull,
)
TIMEOUT = 600.0
TOOL = "stylelint"


def own_config(directory: Path) -> Path | None:
    """Return the component's Stylelint configuration file, as Stylelint would find it."""
    return next((directory / name for name in CONFIGS if (directory / name).is_file()), None)


def _packaged(component: Component, directory: Path) -> list[Diagnostic]:
    try:
        manifest = load_json_object(directory / "package.json")
    except ReportError:
        return []
    if "stylelint" not in manifest:
        return []
    message = "the stylelint field of package.json is not read; move it to .stylelintrc.json"
    path = PurePosixPath(component.root, "package.json").as_posix()
    return [STYLES.diagnostic(message, location=Location(path), component=component.id)]


def _result(target: Target, result: Entry) -> list[Diagnostic]:
    path = target.relative(TOOL, result.get("source"))
    component = target.component.id
    if result.get("ignored") is True:
        message = "the Stylelint configuration ignores this file; every stylesheet is checked"
        return [STYLES.diagnostic(message, location=Location(path), component=component)]
    problems = [
        *findings(TOOL, result.get("warnings", [])),
        *findings(TOOL, result.get("parseErrors", [])),
    ]
    return [
        STYLES.diagnostic(
            str(problem.get("text", "")).strip() or "Stylelint reported a problem",
            location=location(path, number(problem, "line"), number(problem, "column")),
            component=component,
        )
        for problem in problems
    ]


def _options(target: Target, results: list[Entry]) -> list[Diagnostic]:
    texts = {
        str(warning.get("text", "")).strip()
        for result in results
        for warning in findings(TOOL, result.get("invalidOptionWarnings", []))
    }
    return [
        STYLES.diagnostic(f"invalid Stylelint option: {text}", component=target.component.id)
        for text in sorted(texts)
    ]


def stylelint_styles(target: Target, *, node: Node, script: Path, work: Path) -> list[Diagnostic]:
    """Lint the named CSS files; every warning is an error."""
    home = work / "stylelint"
    home.mkdir(parents=True, exist_ok=True)
    own = own_config(target.directory)
    wrapper, report = home / "stylelint.json", home / "report.json"
    wrapper.write_text(
        json.dumps({"extends": [*BASELINE, *([str(own)] if own is not None else [])]}),
        encoding="utf-8",
    )
    report.unlink(missing_ok=True)
    argv = (str(node.executable), str(script), "--config", str(wrapper))
    argv += ("--config-basedir", str(target.directory), *OPTIONS, "--output-file", str(report))
    command = Command(
        (*argv, *target.arguments()),
        cwd=target.directory,
        timeout=TIMEOUT,
        env=node.environ(home / "home"),
    )
    completed = run_tool(command, name=TOOL, accepted={0, 2})
    try:
        results = findings(TOOL, load_json(report))
    except ReportError as error:
        fail(TOOL, f"{error}; {completed.stderr_text()}")
    found = _packaged(target.component, target.directory)
    for result in results:
        found += _result(target, result)
    return found + _options(target, results)


def unsupported_variants(component: Component, paths: tuple[str, ...]) -> list[Diagnostic]:
    """Report each Sass, Less, or Stylus file of a browser component."""
    return [
        STYLES.diagnostic(
            f"{VARIANTS[PurePosixPath(path).suffix.lower()]} is not supported in a browser "
            "component; write CSS or CSS Modules",
            location=Location(path),
            component=component.id,
        )
        for path in paths
    ]
