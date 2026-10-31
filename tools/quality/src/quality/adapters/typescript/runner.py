"""Vitest with V8 coverage, run with a configuration the gate writes (decisions 0005 and 0011).

The gate writes `vitest.quality.mjs` into the run's output directory. It
imports the component's `vitest.config.*` or `vite.config.*`, when there is
one, and overrides what decides the verdict:

- test files are the suite's roots with Vitest's default test-file pattern;
  in-source tests, the component's excludes, projects, and name filters are
  off, `.only` fails, and a run without tests does not pass;
- the reporters are the default one, JUnit into the output directory, and
  the gate's own reporter, which writes `annotations.json`: the tests marked
  `test.fails`, which JUnit reports as passing when they fail as expected,
  the tests that passed only on a retry, and the count of errors raised
  outside any test. Retries are off, but a test's own `retry` option still
  applies, so its retries are reported;
- unit suites measure V8 coverage into LCOV in the output directory.
  `coverage.include` lists the source roots' TypeScript files: Vitest 5 has no
  `coverage.all`, and only included files that no test imports are reported,
  with zero hits. Declaration files are excluded; type-only files report zero
  lines, which the engine treats as not applicable. Other suites measure
  nothing;
- Vite 8 does not transform `.cts` files, so `oxc.include` adds them unless the
  component configures `oxc` itself;
- `resolve.tsconfigPaths` (Vite 8.3) resolves tsconfig `paths` aliases unless
  the component configures it;
- the suites of a browser component, which never include end-to-end ones,
  run in jsdom with `expect.requireAssertions`, so a test that asserts
  nothing fails (decision 0017); a test file's own environment comment still
  applies.

A run without test files exits 1 but still writes a JUnit report without
cases, which the engine reports as `tests.none`, not as a tool failure. Tests
get a new home, cache, and temporary directory, and unit suites reach loopback
only, as for Python.
"""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath

from quality.adapters.native import fail, failure, suite_command
from quality.adapters.typescript.config import TYPESCRIPT_SUFFIXES
from quality.adapters.typescript.environment import Node
from quality.boundedio import JsonValue, ReportError, load_json
from quality.process import Command, ProcessError, run
from quality.testing import Suite, SuiteRequest, SuiteRun

SUITE_TIMEOUT = 3600.0
TOOL = "vitest"
WRAPPER = "vitest.quality.mjs"
RESULTS = "junit.xml"
COVERAGE = "coverage"
LCOV = "lcov.info"
CONFIG_FILES = tuple(
    f"{base}.config.{ext}"
    for base in ("vitest", "vite")
    for ext in ("ts", "mts", "cts", "js", "mjs", "cjs")
)
TEST_FILES = "**/*.{test,spec}.?(c|m)[jt]s?(x)"
SOURCE_FILES = "**/*.{ts,tsx,mts,cts}"
DECLARATION_FILES = "**/*.d.{ts,mts,cts}"
REPORTER = "quality-reporter.mjs"
ANNOTATIONS = "annotations.json"
REPORTER_BODY = """\
import { writeFileSync } from "node:fs";

export default class QualityReporter {
  onTestRunEnd(modules, errors) {
    const inverted = [];
    const retried = [];
    for (const module of modules) {
      for (const test of module.children.allTests()) {
        const name = `${module.relativeModuleId}::${test.fullName}`;
        if (test.options.fails) inverted.push(name);
        if ((test.diagnostic()?.retryCount ?? 0) > 0) retried.push(name);
      }
    }
    writeFileSync(output, JSON.stringify({ inverted, retried, unhandled: errors.length }));
  }
}
"""
WRAPPER_BODY = """\
export default async (env) => {
  const user = loaded.default;
  const base = (typeof user === "function" ? await user(env) : await user) ?? {};
  return {
    ...base,
    oxc: base.oxc === undefined ? { include: /\\.(m?ts|cts|[jt]sx)$/ } : base.oxc,
    resolve: { ...base.resolve, tsconfigPaths: base.resolve?.tsconfigPaths ?? true },
    test: {
      ...base.test,
      projects: undefined,
      testNamePattern: undefined,
      ...settings,
      expect: { ...base.test?.expect, ...settings.expect },
    },
  };
};
"""


def user_config(directory: Path) -> Path | None:
    """Return the configuration file Vitest would load from the component directory."""
    return next((directory / name for name in CONFIG_FILES if (directory / name).is_file()), None)


def coverage_settings(request: SuiteRequest) -> dict[str, JsonValue]:
    """Return the coverage settings of a run: V8 and LCOV for unit suites, else nothing."""
    if request.suite is not Suite.UNIT:
        return {"enabled": False}
    return {
        "enabled": True,
        "provider": "v8",
        "reporter": [["lcovonly", {"file": LCOV}]],
        "reportsDirectory": str(request.output / COVERAGE),
        "include": [f"{root}/{SOURCE_FILES}" for root in request.component.source],
        "exclude": [DECLARATION_FILES],
        "clean": True,
    }


def wrapper(request: SuiteRequest) -> str:
    """Return the configuration module that runs one suite."""
    config = user_config(request.directory)
    load = (
        f"import * as loaded from {json.dumps(str(config))};"
        if config is not None
        else "const loaded = {};"
    )
    settings: dict[str, JsonValue] = {
        "dir": str(request.directory),
        "include": [f"{root}/{TEST_FILES}" for root in request.roots],
        "includeSource": [],
        "exclude": ["**/node_modules/**"],
        "allowOnly": False,
        "passWithNoTests": False,
        "watch": False,
        "retry": 0,
        "reporters": ["default", "junit", str(request.output / REPORTER)],
        "outputFile": {"junit": str(request.output / RESULTS)},
        "coverage": coverage_settings(request),
    }
    if request.component.in_browser:
        settings["environment"] = "jsdom"
        settings["expect"] = {"requireAssertions": True}
    return f"{load}\nconst settings = {json.dumps(settings, indent=2)};\n{WRAPPER_BODY}"


def reporter(request: SuiteRequest) -> str:
    """Return the reporter module that writes the run's annotations."""
    output = json.dumps(str(request.output / ANNOTATIONS))
    return f"const output = {output};\n{REPORTER_BODY}"


def _names(value: JsonValue, key: str) -> frozenset[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        msg = f"{ANNOTATIONS}: '{key}' must be a list of test names"
        raise ReportError(msg)
    return frozenset(item for item in value if isinstance(item, str))


def read_annotations(path: Path) -> tuple[frozenset[str], frozenset[str], int]:
    """Read the inverted and retried tests and the unhandled errors the reporter wrote."""
    value = load_json(path)
    if not isinstance(value, dict):
        msg = f"{ANNOTATIONS} must hold a JSON object"
        raise ReportError(msg)
    unhandled = value.get("unhandled")
    if type(unhandled) is not int or unhandled < 0:
        msg = f"{ANNOTATIONS}: 'unhandled' must be a non-negative integer"
        raise ReportError(msg)
    inverted = _names(value.get("inverted"), "inverted")
    return inverted, _names(value.get("retried"), "retried"), unhandled


def expected(path: str) -> bool:
    """Whether a source file must appear in the coverage report: TypeScript, not declarations."""
    name = PurePosixPath(path)
    return name.suffix in TYPESCRIPT_SUFFIXES and PurePosixPath(name.stem).suffix != ".d"


def run_vitest(node: Node, script: Path, request: SuiteRequest) -> SuiteRun:
    """Run one suite; a run that leaves no results could not run at all."""
    config = request.output / WRAPPER
    config.write_text(wrapper(request), encoding="utf-8")
    (request.output / REPORTER).write_text(reporter(request), encoding="utf-8")
    home = request.output.parent / f"{request.output.name}-home"
    temporary, cache = home / "tmp", home / ".cache"
    temporary.mkdir(parents=True)
    cache.mkdir()
    extra = {"TMPDIR": str(temporary), "XDG_CACHE_HOME": str(cache), "CI": "1"}
    argv = (str(node.executable), str(script), "run", "--config", str(config))
    argv, unisolated = suite_command(request, (*argv, "--root", str(request.directory)))
    command = Command(
        argv,
        cwd=request.directory,
        timeout=SUITE_TIMEOUT,
        env=node.environ(home, extra),
    )
    try:
        completed = run(command)
    except ProcessError as error:
        raise failure(TOOL, str(error)) from error
    results = request.output / RESULTS
    if not results.exists():
        output = completed.stderr_text() or completed.stdout_text()
        fail(TOOL, f"exit status {completed.returncode} without test results: {output}")
    report = request.output / COVERAGE / LCOV if request.suite is Suite.UNIT else None
    inverted, retried, unhandled = read_annotations(request.output / ANNOTATIONS)
    return SuiteRun(
        completed.returncode,
        results,
        report,
        unisolated=unisolated,
        inverted=inverted,
        retried=retried,
        unhandled=unhandled,
    )
