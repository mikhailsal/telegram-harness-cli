from pathlib import Path

import pytest

from quality.adapters import native
from quality.adapters.dart.environment import Sdk
from quality.adapters.dart.runner import (
    GENERATED,
    LCOV,
    RESULTS,
    Generated,
    filled,
    generated_test,
    run_dart_test,
    source_files,
    suite_argv,
)
from quality.adapters.dart.structure import DartFile
from quality.adapters.native import ToolFailedError
from quality.coverage import CoverageReport, FileCoverage
from quality.metrics import FileMetrics
from quality.policy import Component
from quality.testing import ResultsFormat, Suite, SuiteRequest
from quality.testkit.dartfakes import FakeDart, dart_component
from quality.testkit.suites import direct

# The fake `dart test` writes a results file where the JSON file reporter would.
RESULTS_FILE = """\
for arg in "$@"; do
  case "$arg" in --file-reporter=json:*) results="${arg#--file-reporter=json:}" ;; esac
done
echo '{"type":"done","success":true}' > "$results"
env > "$HOME/env"
"""
LCOV_FILE = """\
for arg in "$@"; do case "$arg" in --out=*) out="${arg#--out=}" ;; esac; done
echo 'end_of_record' > "$out"
"""


def request(
    tmp_path: Path,
    suite: Suite = Suite.UNIT,
    component: Component | None = None,
    roots: tuple[str, ...] = ("test",),
) -> SuiteRequest:
    work = tmp_path / "work"
    output = work / f"app-{suite}"
    output.mkdir(parents=True)
    (tmp_path / "snapshot" / "app").mkdir(parents=True, exist_ok=True)
    component = component or dart_component()
    return SuiteRequest(component, suite, roots, tmp_path / "snapshot", output, 0.0, work)


def dart_file(path: str, *, part: bool = False, edges: tuple[str, ...] = ()) -> DartFile:
    return DartFile(path, part, FileMetrics(path, (), ()), dict.fromkeys(edges, 1))


def write(path: Path, text: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_source_files_skip_test_roots_tool_directories_and_links(tmp_path: Path) -> None:
    component = dart_component(source=("lib", "bin", "tool"), integration=("lib/it",))
    unit = request(tmp_path, component=component)
    for name in ("lib/b.dart", "lib/src/a.dart", "bin/main.dart", "lib/it/x_test.dart"):
        write(unit.directory / name)
    write(unit.directory / "lib" / ".dart_tool" / "quality" / "x.dart")
    write(unit.directory / "lib" / "notes.md")
    write(unit.directory / "test" / "a_test.dart")
    (unit.directory / "lib" / "link.dart").symlink_to(unit.directory / "lib" / "b.dart")
    assert source_files(unit) == ("app/bin/main.dart", "app/lib/b.dart", "app/lib/src/a.dart")


def test_the_generated_test_imports_every_library_and_reaches_their_parts(
    tmp_path: Path,
) -> None:
    unit = request(tmp_path)
    files = [
        dart_file("app/lib/a.dart", edges=("app/lib/src/p.dart", "app/lib/b.dart", "app/x.dart")),
        dart_file("app/lib/b.dart", edges=("app/lib/src/p.dart",)),
        dart_file("app/lib/src/p.dart", part=True, edges=("app/lib/src/q.dart",)),
        dart_file("app/lib/src/q.dart", part=True, edges=("app/lib/src/p.dart",)),
        dart_file("app/lib/src/orphan.dart", part=True),
        dart_file("app/lib/odd name.dart"),
        dart_file("app/bin/main.dart"),
    ]
    generated = generated_test(unit, "calc", files)
    assert generated.path.name == GENERATED
    assert generated.path.parent.parent == unit.directory / ".dart_tool" / "quality"
    assert generated.path.parent.name.startswith("run-")
    assert generated.reached == {
        "app/bin/main.dart",
        "app/lib/a.dart",
        "app/lib/b.dart",
        "app/lib/odd name.dart",
        "app/lib/src/p.dart",
        "app/lib/src/q.dart",
    }
    lines = generated.path.read_text().splitlines()
    assert lines[1:] == [
        "import '../../../bin/main.dart' as library0;",
        "import 'package:calc/a.dart' as library1;",
        "import 'package:calc/b.dart' as library2;",
        "import 'package:calc/odd%20name.dart' as library3;",
        "",
        "void main() {}",
    ]
    assert generated_test(unit, "calc", []).path.parent != generated.path.parent


def test_unit_runs_collect_branch_coverage_and_other_suites_do_not(
    tmp_path: Path, sdk: Sdk
) -> None:
    unit = request(tmp_path, roots=("test", "test/more"))
    generated = Generated(
        unit.directory / ".dart_tool" / "quality" / "run-1" / GENERATED, frozenset()
    )
    assert suite_argv(sdk, unit, generated) == (
        str(sdk.executable),
        "--suppress-analytics",
        "test",
        "--platform",
        "vm",
        "--no-retry",
        "--branch-coverage",
        f"--coverage={unit.output / 'raw'}",
        "--reporter=failures-only",
        f"--file-reporter=json:{unit.output / RESULTS}",
        "./test",
        "./test/more",
        f".dart_tool/quality/run-1/{GENERATED}",
    )
    other = request(tmp_path, Suite.INTEGRATION, roots=("integration",))
    assert suite_argv(sdk, other, None)[3:] == (
        "--platform",
        "vm",
        "--no-retry",
        "--reporter=failures-only",
        f"--file-reporter=json:{other.output / RESULTS}",
        "./integration",
    )


def test_a_clean_unit_run_formats_its_coverage(tmp_path: Path, fake_dart: FakeDart) -> None:
    fake_dart.handle("test", RESULTS_FILE)
    fake_dart.handle("run", LCOV_FILE)
    component = dart_component(source=("lib", "bin"))
    unit = request(tmp_path, component=component)
    generated = generated_test(unit, "calc", [])
    run = run_dart_test(fake_dart.sdk, unit, generated)
    assert run.returncode == 0
    assert run.results == unit.output / RESULTS
    assert run.coverage == unit.output / LCOV
    assert (unit.output / LCOV).read_text() == "end_of_record\n"
    assert run.results_format is ResultsFormat.DART_JSON
    home = unit.output.parent / f"{unit.output.name}-home"
    env = dict(
        line.split("=", 1) for line in (home / "env").read_text().splitlines() if "=" in line
    )
    assert (env["HOME"], env["TMPDIR"], env["CI"]) == (str(home), str(home / "tmp"), "1")
    assert env["XDG_CACHE_HOME"] == str(home / ".cache")
    test, format_ = fake_dart.calls()
    assert test[0] == format_[0] == str(unit.directory)
    assert format_[2:] == [
        "run",
        "coverage:format_coverage",
        "--lcov",
        f"--in={unit.output / 'raw'}",
        f"--out={unit.output / LCOV}",
        f"--package={unit.directory}",
        f"--report-on={unit.directory / 'lib'}",
        f"--report-on={unit.directory / 'bin'}",
    ]


@pytest.mark.parametrize(("suite", "status"), [(Suite.UNIT, 1), (Suite.INTEGRATION, 0)])
def test_failing_and_other_runs_are_not_formatted(
    tmp_path: Path, fake_dart: FakeDart, suite: Suite, status: int
) -> None:
    fake_dart.handle("test", RESULTS_FILE + f"exit {status}\n")
    suite_request = request(tmp_path, suite)
    generated = generated_test(suite_request, "calc", []) if suite is Suite.UNIT else None
    run = run_dart_test(fake_dart.sdk, suite_request, generated)
    assert (run.returncode, run.coverage) == (status, None)
    assert fake_dart.commands() == ["test"]


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (
            "echo 'Could not find package test' >&2\nexit 65\n",
            "exit status 65 without test results: Could not find package test",
        ),
        ("echo 'no stderr'\nexit 1\n", "exit status 1 without test results: no stderr"),
    ],
)
def test_a_run_without_results_is_a_tool_failure(
    tmp_path: Path, fake_dart: FakeDart, body: str, message: str
) -> None:
    fake_dart.handle("test", body)
    with pytest.raises(ToolFailedError, match=message):
        run_dart_test(fake_dart.sdk, request(tmp_path, Suite.INTEGRATION), None)


def test_a_suite_without_roots_or_an_sdk_is_a_tool_failure(tmp_path: Path) -> None:
    sdk = Sdk(tmp_path / "missing" / "dart", tmp_path / "cache")
    with pytest.raises(ToolFailedError, match="the unit suite of 'app' has no roots"):
        run_dart_test(sdk, request(tmp_path, roots=()), None)
    with pytest.raises(ToolFailedError, match=r"dart test: .*missing/dart"):
        run_dart_test(sdk, request(tmp_path, Suite.INTEGRATION), None)


def test_a_failed_coverage_format_is_a_tool_failure(tmp_path: Path, fake_dart: FakeDart) -> None:
    fake_dart.handle("test", RESULTS_FILE)
    fake_dart.handle("run", "echo 'no raw data' >&2\nexit 1\n")
    unit = request(tmp_path)
    with pytest.raises(ToolFailedError, match="format_coverage: exit status 1: no raw data"):
        run_dart_test(fake_dart.sdk, unit, generated_test(unit, "calc", []))


def test_compiled_files_without_coverable_lines_are_filled_in() -> None:
    measured = FileCoverage("app/lib/a.dart", frozenset({1, 2}), frozenset({1}))
    report = CoverageReport({"app/lib/a.dart": measured}, branches_measured=True)
    result = filled(report, frozenset({"app/lib/a.dart", "app/lib/types.dart"}))
    assert result.branches_measured
    assert dict(result.files) == {
        "app/lib/a.dart": measured,
        "app/lib/types.dart": FileCoverage("app/lib/types.dart", frozenset(), frozenset()),
    }


def test_a_unit_run_says_why_it_had_the_network(
    tmp_path: Path, fake_dart: FakeDart, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(native, "contained", direct("no namespaces"))
    fake_dart.handle("test", RESULTS_FILE + "exit 1\n")
    unit = request(tmp_path)
    assert run_dart_test(fake_dart.sdk, unit, generated_test(unit, "calc", [])).unisolated == (
        "no namespaces"
    )
    integration = request(tmp_path / "other", Suite.INTEGRATION)
    assert run_dart_test(fake_dart.sdk, integration, None).unisolated is None
