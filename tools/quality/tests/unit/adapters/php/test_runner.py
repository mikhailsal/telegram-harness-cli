from pathlib import Path
from types import MappingProxyType

import pytest

from quality.adapters import native
from quality.adapters.native import ToolFailedError
from quality.adapters.php.runner import (
    BINARY,
    BOOTSTRAP,
    CLOVER,
    JSONL,
    RESULTS,
    run_phpunit,
    suite_argv,
    unmeasured,
)
from quality.adapters.php.runtime import Php
from quality.adapters.php.structure import PhpFile
from quality.coverage import Counter, CoverageReport, FileCoverage
from quality.metrics import FileMetrics
from quality.testing import ResultsFormat, Suite
from quality.testkit.phpfakes import FakePhp, php_component, suite_request
from quality.testkit.suites import direct

# The fake PHPUnit writes the results, and for a unit run the Clover report, where asked.
REPORTS = """\
for arg in "$@"; do
  case "$arg" in
    --log-junit=*) results="${arg#--log-junit=}" ;;
    --coverage-clover=*) clover="${arg#--coverage-clover=}" ;;
  esac
done
echo '<testsuites/>' > "$results"
[ -z "$clover" ] || echo '<coverage/>' > "$clover"
env > "$HOME/env"
"""
# PHPUnit 13.4.3 under PCOV with branch coverage on: an empty results file and a warning.
REFUSES = """\
for arg in "$@"; do
  case "$arg" in --log-junit=*) : > "${arg#--log-junit=}" ;; esac
done
printf 'There was 1 PHPUnit test runner warning:\\n\\n'
echo '1) No code coverage driver available that supports line and branch coverage'
echo
echo 'No tests executed!'
exit 1
"""


def php_file(path: str, *, loadable: bool = True, units: int = 1) -> PhpFile:
    return PhpFile(path, loadable, units, FileMetrics(path, (), ()), {})


def test_only_unit_runs_collect_both_reports_with_xdebug(tmp_path: Path, php: Php) -> None:
    unit = suite_request(tmp_path)
    settings = unit.output / "phpunit.xml"
    unloaded = Php(php.executable, php.version, False, php.composer, php.cache)
    assert suite_argv(unloaded, unit, settings) == (
        *unloaded.command(coverage=True),
        str(unit.directory / BINARY),
        f"--configuration={settings}",
        f"--log-junit={unit.output / RESULTS}",
        "--no-progress",
        f"--coverage-clover={unit.output / CLOVER}",
        f"--coverage-jsonl={unit.output / JSONL}",
    )
    integration = suite_request(tmp_path / "other", Suite.INTEGRATION)
    assert suite_argv(unloaded, integration, settings) == (
        *unloaded.command(),
        str(integration.directory / BINARY),
        f"--configuration={settings}",
        f"--log-junit={integration.output / RESULTS}",
        "--no-progress",
    )


def test_a_unit_run_returns_its_results_and_clover_report(
    tmp_path: Path, fake_php: FakePhp
) -> None:
    fake_php.handle("phpunit", REPORTS + "exit 1\n")
    unit = suite_request(tmp_path)
    run = run_phpunit(fake_php.php, unit, [php_file("app/src/A.php")])
    assert run.returncode == 1
    assert run.results == unit.output / RESULTS
    assert run.coverage == unit.output / CLOVER
    assert run.results_format is ResultsFormat.JUNIT
    assert run.unisolated is None
    # PHPUnit exits 1 when a test failed and 2 when one raised an error.
    assert run.failure_status == frozenset({1, 2})
    ((cwd, *_),) = fake_php.calls()
    assert cwd == str(unit.directory)
    home = unit.output.parent / f"{unit.output.name}-home"
    env = dict(
        line.split("=", 1) for line in (home / "env").read_text().splitlines() if "=" in line
    )
    assert (env["HOME"], env["TMPDIR"]) == (str(home), str(home / "tmp"))
    assert (env["XDG_CACHE_HOME"], env["CI"], env["XDEBUG_MODE"]) == (
        str(home / ".cache"),
        "1",
        "coverage",
    )
    assert "app/src/A.php" in (unit.output / BOOTSTRAP).read_text()


def test_an_integration_run_has_no_coverage(tmp_path: Path, fake_php: FakePhp) -> None:
    fake_php.handle("phpunit", REPORTS)
    component = php_component(integration=("tests-it",))
    integration = suite_request(tmp_path, Suite.INTEGRATION, component, roots=("tests-it",))
    run = run_phpunit(fake_php.php, integration, ())
    assert (run.returncode, run.coverage) == (0, None)
    home = integration.output.parent / f"{integration.output.name}-home"
    assert "XDEBUG_MODE=off" in (home / "env").read_text().splitlines()


def test_a_unit_run_says_why_it_had_the_network(
    tmp_path: Path, fake_php: FakePhp, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(native, "contained", direct("no namespaces"))
    fake_php.handle("phpunit", REPORTS)
    assert run_phpunit(fake_php.php, suite_request(tmp_path), ()).unisolated == "no namespaces"


def test_a_unit_run_without_a_clover_report_has_no_coverage(
    tmp_path: Path, fake_php: FakePhp
) -> None:
    fake_php.handle("phpunit", REPORTS.replace("[ -z", "true || [ -z"))
    assert run_phpunit(fake_php.php, suite_request(tmp_path), ()).coverage is None


def test_a_run_without_results_or_roots_is_a_tool_failure(
    tmp_path: Path, fake_php: FakePhp
) -> None:
    fake_php.handle("phpunit", "echo 'No code coverage driver available' >&2\nexit 1\n")
    with pytest.raises(ToolFailedError, match="exit status 1 without test results: No code"):
        run_phpunit(fake_php.php, suite_request(tmp_path), ())
    with pytest.raises(ToolFailedError, match="the unit suite of 'app' has no roots"):
        run_phpunit(fake_php.php, suite_request(tmp_path / "other", roots=()), ())


def test_a_run_that_leaves_empty_results_quotes_why_phpunit_refused(
    tmp_path: Path, fake_php: FakePhp
) -> None:
    fake_php.handle("phpunit", REFUSES)
    with pytest.raises(ToolFailedError) as raised:
        run_phpunit(fake_php.php, suite_request(tmp_path), ())
    assert raised.value.diagnostic.message == (
        "phpunit: exit status 1 without test results: There was 1 PHPUnit test runner "
        "warning:\n\n1) No code coverage driver available that supports line and branch coverage"
        "\n\nNo tests executed!"
    )


def test_a_run_that_cannot_start_is_a_tool_failure(tmp_path: Path, php: Php) -> None:
    missing = Php(tmp_path / "missing", php.version, True, php.composer, php.cache)
    with pytest.raises(ToolFailedError, match="phpunit: "):
        run_phpunit(missing, suite_request(tmp_path), ())


def test_files_with_functions_but_no_branches_are_unmeasured() -> None:
    def file(path: str, branches: Counter) -> FileCoverage:
        return FileCoverage(path, frozenset({1}), frozenset(), branches)

    files = {
        "app/src/Loaded.php": file("app/src/Loaded.php", Counter(1, 2)),
        "app/src/Never.php": file("app/src/Never.php", Counter(0, 0)),
        "app/src/Script.php": file("app/src/Script.php", Counter(0, 0)),
    }
    report = CoverageReport(MappingProxyType(files), branches_measured=True)
    found = unmeasured(report, {"app/src/Loaded.php": 2, "app/src/Never.php": 1})
    assert found.branches_measured
    assert {path: item.branches_measured for path, item in found.files.items()} == {
        "app/src/Loaded.php": True,
        "app/src/Never.php": False,
        "app/src/Script.php": True,
    }
