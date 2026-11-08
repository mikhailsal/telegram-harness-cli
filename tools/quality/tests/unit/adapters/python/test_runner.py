import json
import sys
from pathlib import Path

import pytest

from quality.adapters import native
from quality.adapters.python.runner import (
    coverage_settings,
    pytest_argv,
    run_pytest,
    shared_caches,
    suite_environment,
)
from quality.policy import Component
from quality.testing import Suite, SuiteRequest
from quality.testkit.policies import ComponentSpec, build_policy
from quality.testkit.pythonproject import python_project
from quality.testkit.suites import direct

CORE = "def sign(value: int) -> int:\n    if value < 0:\n        return -1\n    return 1\n"
PASSING = "from app.core import sign\n\n\ndef test_sign() -> None:\n    assert sign(-2) == -1\n"
FAILING = "def test_fails() -> None:\n    assert False\n"


def component() -> Component:
    (found,) = build_policy(ComponentSpec(root="app", integration=("integration",))).components
    return found


def request(
    tmp_path: Path, suite: Suite = Suite.UNIT, roots: tuple[str, ...] = ("tests",)
) -> SuiteRequest:
    work = tmp_path / "work"
    output = work / f"app-{suite}"
    output.mkdir(parents=True)
    return SuiteRequest(component(), suite, roots, tmp_path / "snapshot", output, 0.0, work)


def script(path: Path, body: str) -> Path:
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(0o755)
    return path


def runner_failure(python: Path, tmp_path: Path) -> str:
    unit = request(tmp_path)
    unit.directory.mkdir(parents=True)
    with pytest.raises(native.ToolFailedError) as raised:
        run_pytest(python, unit, "pyproject.toml")
    return raised.value.diagnostic.message


def test_coverage_settings_measure_branches_into_the_output(tmp_path: Path) -> None:
    expected = f"[run]\nbranch = True\ndata_file = {tmp_path / '.coverage'}\n"
    assert coverage_settings(tmp_path) == expected


def test_unit_runs_measure_the_source_roots(tmp_path: Path) -> None:
    unit = request(tmp_path)
    directory, output = unit.directory, unit.output
    assert pytest_argv(Path("/env/python"), unit, "pytest.ini") == (
        "/env/python",
        "-m",
        "pytest",
        "-c",
        f"{directory}/pytest.ini",
        f"--rootdir={directory}",
        "-p",
        "no:cacheprovider",
        "-q",
        f"--junitxml={output}/junit.xml",
        "-o",
        "xfail_strict=true",
        "-p",
        "no:rerunfailures",
        "-p",
        "no:flaky",
        f"--cov={directory}/src",
        "--cov-branch",
        f"--cov-config={output}/coveragerc",
        f"--cov-report=json:{output}/coverage.json",
        f"{directory}/tests",
    )


def test_other_suites_measure_nothing(tmp_path: Path) -> None:
    integration = request(tmp_path, Suite.INTEGRATION, ("integration",))
    argv = pytest_argv(Path("/env/python"), integration, "pyproject.toml")
    assert not [arg for arg in argv if arg.startswith("--cov")]
    assert argv[-1] == f"{integration.directory}/integration"


def test_suites_get_a_new_home_and_temporary_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PYTHONPATH", "/elsewhere")
    unit = request(tmp_path)
    env = suite_environment(unit)
    home = unit.output.parent / f"{unit.output.name}-home"
    assert (env["HOME"], env["TMPDIR"]) == (str(home), str(home / "tmp"))
    assert (env["XDG_CACHE_HOME"], env["PYTHONDONTWRITEBYTECODE"]) == (str(home / ".cache"), "1")
    assert (home / "tmp").is_dir()
    assert "PYTHONPATH" not in env


def test_suites_share_only_the_toolchain_and_package_caches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = tmp_path / "real-cache"
    (real / "quality").mkdir(parents=True)
    (real / "pip").mkdir()
    monkeypatch.setenv("XDG_CACHE_HOME", str(real))
    monkeypatch.delenv("UV_CACHE_DIR", raising=False)
    cache = Path(suite_environment(request(tmp_path))["XDG_CACHE_HOME"])
    assert sorted(path.name for path in cache.iterdir()) == ["quality"]
    assert (cache / "quality").resolve() == real / "quality"
    packages = tmp_path / "uv-cache"
    packages.mkdir()
    monkeypatch.setenv("UV_CACHE_DIR", str(packages))
    cache = Path(suite_environment(request(tmp_path / "again"))["XDG_CACHE_HOME"])
    assert (cache / "uv").resolve() == packages


def test_shared_caches_default_to_the_home_cache() -> None:
    home = Path("/home/someone")
    assert shared_caches({"HOME": str(home)}) == {
        "quality": home / ".cache" / "quality",
        "uv": home / ".cache" / "uv",
    }


def test_a_unit_run_writes_results_and_coverage(tmp_path: Path) -> None:
    unit = request(tmp_path)
    python_project(unit.directory, {"src/app/core.py": CORE, "tests/test_core.py": PASSING})
    done = run_pytest(Path(sys.executable), unit, "pyproject.toml")
    assert (done.returncode, done.results) == (0, unit.output / "junit.xml")
    assert done.coverage == unit.output / "coverage.json"
    report = json.loads(done.coverage.read_bytes() if done.coverage else b"")
    assert report["meta"]["branch_coverage"] is True
    assert report["files"]["src/app/core.py"]["summary"]["missing_lines"] == 1
    assert (unit.output / "coveragerc").read_text() == coverage_settings(unit.output)


def test_the_coverage_of_parallel_workers_is_combined(tmp_path: Path) -> None:
    unit = request(tmp_path)
    negative = "from app.core import sign\n\n\ndef test_negative() -> None:\n"
    positive = "from app.core import sign\n\n\ndef test_positive() -> None:\n"
    files = {
        "src/app/core.py": CORE,
        "tests/test_negative.py": negative + "    assert sign(-2) == -1\n",
        "tests/test_positive.py": positive + "    assert sign(3) == 1\n",
    }
    python_project(unit.directory, files)
    settings = unit.directory / "pyproject.toml"
    # Each file runs on its own worker, so only the combined data covers both branches.
    workers = 'addopts = ["-n", "2", "--dist", "loadfile"]\n'
    settings.write_text(settings.read_text() + workers)
    done = run_pytest(Path(sys.executable), unit, "pyproject.toml")
    assert done.returncode == 0
    assert done.results.read_text().count("<testcase ") == 2
    report = json.loads(done.coverage.read_bytes() if done.coverage else b"")
    summary = report["files"]["src/app/core.py"]["summary"]
    assert (summary["missing_lines"], summary["missing_branches"]) == (0, 0)


def test_a_failing_suite_reports_its_status_without_coverage(tmp_path: Path) -> None:
    integration = request(tmp_path, Suite.INTEGRATION, ("integration",))
    python_project(integration.directory, {"integration/test_fails.py": FAILING})
    done = run_pytest(Path(sys.executable), integration, "pyproject.toml")
    assert (done.returncode, done.coverage) == (1, None)
    assert done.results.is_file()


def test_a_run_without_results_fails(tmp_path: Path) -> None:
    python = script(tmp_path / "python", 'echo "no module named pytest" >&2\nexit 4')
    message = runner_failure(python, tmp_path)
    assert message == "pytest: exit status 4 without test results: no module named pytest"


def test_a_silent_run_reports_its_output(tmp_path: Path) -> None:
    python = script(tmp_path / "python", 'echo "usage: pytest"\nexit 5')
    message = runner_failure(python, tmp_path)
    assert message == "pytest: exit status 5 without test results: usage: pytest"


def test_an_interpreter_that_cannot_start_fails(tmp_path: Path) -> None:
    message = runner_failure(tmp_path / "missing-python", tmp_path)
    assert message.startswith("pytest: ")
    assert "missing-python" in message


def test_a_unit_run_says_why_it_had_the_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(native, "contained", direct("no namespaces"))
    unit = request(tmp_path)
    unit.directory.mkdir(parents=True)
    python = script(tmp_path / "python", f"touch {unit.output / 'junit.xml'}")
    assert run_pytest(python, unit, "pyproject.toml").unisolated == "no namespaces"
