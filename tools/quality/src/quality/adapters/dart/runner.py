"""package:test with VM coverage, formatted by the component's package:coverage (decision 0012).

Unit suites run `dart test --platform vm --branch-coverage --coverage=<raw>`
over the suite's roots and one generated test. The generated test is written
only in the snapshot, as `all_libraries_test.dart` in a new directory below the
component's `.dart_tool/quality`, where `package:` imports resolve as in the
component's own tests; snapshots never hold `.dart_tool`, so it cannot collide
with a candidate file. It imports every library of the source roots, each under
its own prefix, and declares no test. The VM compiles every loaded library, so
a file no test imports is reported with zero hits. Part files belong to their
library and are not imported; the helper tells them apart.

The component's locked package:coverage then formats the raw data with
`format_coverage --lcov`, `--report-on` naming each source root, so `bin/`
scripts are measured as well as `lib/`. `--check-ignore` is not passed, so
`coverage:ignore` comments have no effect.

After a clean unit run, a source file that the report lacks was compiled
through the generated test, directly or as a part of an imported library, and
has no coverable line: Dart reports only function entries, calls, and block
entries (decision 0005). `coverage()` adds such a file as measured with no
lines and no branches, which the engine treats as not applicable, like a
TypeScript declaration file. A file the generated test did not reach, such as
a part that no library names, stays missing and fails.

Other suites run `dart test --platform vm` over their roots without coverage.
Every run writes the JSON reporter's events to a results file; a run that
writes none could not run at all. Roots are passed as `./<root>`, so none can
be read as an option and the suite paths in the report stay relative. A
missing root is a failing load case, and a run without tests exits 79 with a
report without cases, which the engine reports as `tests.none`. Each run gets a
new home, cache, and temporary directory, as for Python, and is reaped when it
exits, so no VM service outlives it. A unit suite reaches loopback only where
the system allows it (decision 0008); the VM service needs nothing else.
Every run passes `--no-retry`, so a test's `retry` option cannot turn a flaky
failure into a pass.
"""

from __future__ import annotations

import posixpath
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from urllib.parse import quote

from quality.adapters.dart.environment import Sdk
from quality.adapters.dart.structure import DartFile
from quality.adapters.native import fail, failure, run_tool, suite_command
from quality.coverage import CoverageReport, FileCoverage
from quality.process import Command, ProcessError, run
from quality.testing import ResultsFormat, SuiteRequest, SuiteRun

SUITE_TIMEOUT = 3600.0
FORMAT_TIMEOUT = 600.0
TOOL = "dart test"
GENERATED = "all_libraries_test.dart"
GENERATED_DIRECTORY = (".dart_tool", "quality")
RESULTS = "results.json"
RAW = "raw"
LCOV = "lcov.info"
LIBRARY = "lib"


@dataclass(frozen=True, slots=True)
class Generated:
    """The generated test of a unit run and the source files it makes the VM compile."""

    path: Path
    reached: frozenset[str]


def source_files(request: SuiteRequest) -> tuple[str, ...]:
    """Return the Dart files of the source roots, without test roots and `.dart_tool`."""
    component = request.component
    tests = [PurePosixPath(component.root, root) for root in component.tests.all_paths()]
    found: set[str] = set()
    for root in component.source:
        for path in (request.directory / root).rglob("*.dart"):
            name = PurePosixPath(path.relative_to(request.snapshot).as_posix())
            skipped = GENERATED_DIRECTORY[0] in name.parts or path.is_symlink()
            if not skipped and not any(name.is_relative_to(test) for test in tests):
                found.add(name.as_posix())
    return tuple(sorted(found))


def _reached(files: Mapping[str, DartFile], libraries: Iterable[str]) -> frozenset[str]:
    """Return the libraries and, transitively, the parts their part directives name."""
    reached = set(libraries)
    pending = list(reached)
    while pending:
        for target in files[pending.pop()].edges:
            known = files.get(target)
            if known is not None and known.part and target not in reached:
                reached.add(target)
                pending.append(target)
    return frozenset(reached)


def _uri(request: SuiteRequest, package: str, path: str, directory: Path) -> str:
    library = PurePosixPath(request.component.root, LIBRARY)
    name = PurePosixPath(path)
    if name.is_relative_to(library):
        return f"package:{package}/{quote(name.relative_to(library).as_posix())}"
    absolute = (request.snapshot / path).as_posix()
    return quote(posixpath.relpath(absolute, directory.as_posix()))


def generated_test(request: SuiteRequest, package: str, files: Iterable[DartFile]) -> Generated:
    """Write the test that imports every library of the source roots."""
    by_path = {file.path: file for file in files}
    libraries = sorted(path for path, file in by_path.items() if not file.part)
    parent = request.directory.joinpath(*GENERATED_DIRECTORY)
    parent.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="run-", dir=parent))
    imports = [
        f"import '{_uri(request, package, path, directory)}' as library{index};\n"
        for index, path in enumerate(libraries)
    ]
    header = "// Generated by the quality gate: every library, compiled for coverage.\n"
    path = directory / GENERATED
    path.write_text(header + "".join(imports) + "\nvoid main() {}\n", encoding="utf-8")
    return Generated(path, _reached(by_path, libraries))


def suite_argv(sdk: Sdk, request: SuiteRequest, generated: Generated | None) -> tuple[str, ...]:
    """Return the `dart test` command of one suite; only unit runs collect coverage."""
    output = request.output
    coverage = ("--branch-coverage", f"--coverage={output / RAW}") if generated else ()
    reports = ("--reporter=failures-only", f"--file-reporter=json:{output / RESULTS}")
    roots = [posixpath.join(".", root) for root in request.roots]
    extra = [posixpath.relpath(generated.path, request.directory)] if generated else []
    options = ("--platform", "vm", "--no-retry", *coverage, *reports)
    return sdk.command("test", *options, *roots, *extra)


def _format_coverage(sdk: Sdk, request: SuiteRequest, env: Mapping[str, str]) -> Path:
    output = request.output / LCOV
    sources = (f"--report-on={request.directory / root}" for root in request.component.source)
    argv = sdk.command(
        "run",
        "coverage:format_coverage",
        "--lcov",
        f"--in={request.output / RAW}",
        f"--out={output}",
        f"--package={request.directory}",
        *sources,
    )
    command = Command(argv, cwd=request.directory, timeout=FORMAT_TIMEOUT, env=env, reap=True)
    run_tool(command, name="format_coverage", accepted={0})
    return output


def run_dart_test(sdk: Sdk, request: SuiteRequest, generated: Generated | None) -> SuiteRun:
    """Run one suite; a run that leaves no results could not run at all."""
    if not request.roots:
        fail(TOOL, f"the {request.suite} suite of '{request.component.id}' has no roots")
    home = request.output.parent / f"{request.output.name}-home"
    temporary, cache = home / "tmp", home / ".cache"
    temporary.mkdir(parents=True)
    cache.mkdir()
    extra = {"TMPDIR": str(temporary), "XDG_CACHE_HOME": str(cache), "CI": "1"}
    env = sdk.environ(home, extra)
    argv, unisolated = suite_command(request, suite_argv(sdk, request, generated))
    command = Command(argv, cwd=request.directory, timeout=SUITE_TIMEOUT, env=env, reap=True)
    try:
        completed = run(command)
    except ProcessError as error:
        raise failure(TOOL, str(error)) from error
    results = request.output / RESULTS
    if not results.is_file():
        output = completed.stderr_text() or completed.stdout_text()
        fail(TOOL, f"exit status {completed.returncode} without test results: {output}")
    clean = generated is not None and completed.returncode == 0
    report = _format_coverage(sdk, request, env) if clean else None
    return SuiteRun(
        completed.returncode,
        results,
        report,
        results_format=ResultsFormat.DART_JSON,
        unisolated=unisolated,
    )


def filled(report: CoverageReport, reached: frozenset[str]) -> CoverageReport:
    """Add each compiled file the report lacks as measured with nothing to cover."""
    missing = {path: FileCoverage(path, frozenset(), frozenset()) for path in reached}
    return replace(report, files=MappingProxyType({**missing, **report.files}))
