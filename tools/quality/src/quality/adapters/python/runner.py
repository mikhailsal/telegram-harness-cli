"""pytest with coverage.py, run from a component's locked environment (decision 0009).

The runner writes its own coverage settings, so `[tool.coverage]` and
`.coveragerc` in the candidate cannot omit files or exclude lines. Tests get a
new home, cache, and temporary directory inside the run's work area; only the
pinned toolchains and uv's package cache are shared, so offline tools stay usable.
Unit suites reach loopback only where the system allows it (decision 0008).
An unexpected pass of an expected failure fails, and rerun plugins are disabled.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from quality.adapters.native import fail, failure, suite_command
from quality.process import Command, ProcessError, run, sanitized
from quality.testing import Suite, SuiteRequest, SuiteRun

# A hang guard, not a budget: the core's acceptance suite drives nested gates
# through real hooks, about a quarter of an hour on eight CPUs and about an hour
# without parallel workers.
SUITE_TIMEOUT = 7200.0
RESULTS = "junit.xml"
COVERAGE = "coverage.json"
RCFILE = "coveragerc"
TOOL = "pytest"
# Plugins that rerun failed tests; a rerun would hide a flaky test (decision 0014).
RETRIES = ("rerunfailures", "flaky")


def coverage_settings(output: Path) -> str:
    """Return the only coverage settings a run uses: branches on, data kept in the output."""
    return f"[run]\nbranch = True\ndata_file = {output / '.coverage'}\n"


def pytest_argv(python: Path, request: SuiteRequest, config: str) -> tuple[str, ...]:
    """Build the pytest command; only unit runs measure coverage."""
    directory, output = request.directory, request.output
    argv = (str(python), "-m", "pytest", "-c", str(directory / config), f"--rootdir={directory}")
    argv += ("-p", "no:cacheprovider", "-q", f"--junitxml={output / RESULTS}")
    argv += (
        "-o",
        "xfail_strict=true",
        *(item for name in RETRIES for item in ("-p", f"no:{name}")),
    )
    if request.suite is Suite.UNIT:
        argv += tuple(f"--cov={directory / root}" for root in request.component.source)
        argv += ("--cov-branch", f"--cov-config={output / RCFILE}")
        argv += (f"--cov-report=json:{output / COVERAGE}",)
    return (*argv, *(str(directory / root) for root in request.roots))


def shared_caches(environ: Mapping[str, str]) -> dict[str, Path]:
    """Return the caches a suite may reach: the pinned toolchains and uv's packages."""
    home = Path(environ.get("HOME") or Path.home())
    base = Path(environ.get("XDG_CACHE_HOME") or home / ".cache")
    uv = environ.get("UV_CACHE_DIR")
    return {"quality": base / "quality", "uv": Path(uv) if uv else base / "uv"}


def suite_environment(request: SuiteRequest) -> dict[str, str]:
    """Return a sanitized environment with a new home, cache, and temporary directory."""
    home = request.output.parent / f"{request.output.name}-home"
    temporary, cache = home / "tmp", home / ".cache"
    temporary.mkdir(parents=True)
    cache.mkdir()
    for name, target in shared_caches(os.environ).items():
        if target.is_dir():
            (cache / name).symlink_to(target, target_is_directory=True)
    extra = {
        "HOME": str(home),
        "TMPDIR": str(temporary),
        "XDG_CACHE_HOME": str(cache),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    return sanitized(extra=extra)


def run_pytest(python: Path, request: SuiteRequest, config: str) -> SuiteRun:
    """Run one suite; a run that leaves no results could not run at all."""
    (request.output / RCFILE).write_text(coverage_settings(request.output), encoding="utf-8")
    argv, unisolated = suite_command(request, pytest_argv(python, request, config))
    command = Command(
        argv,
        cwd=request.directory,
        timeout=SUITE_TIMEOUT,
        env=suite_environment(request),
    )
    try:
        completed = run(command)
    except ProcessError as error:
        raise failure(TOOL, str(error)) from error
    results = request.output / RESULTS
    if not results.exists():
        output = completed.stderr_text() or completed.stdout_text()
        fail(TOOL, f"exit status {completed.returncode} without test results: {output}")
    report = request.output / COVERAGE if request.suite is Suite.UNIT else None
    return SuiteRun(completed.returncode, results, report, unisolated=unisolated)
