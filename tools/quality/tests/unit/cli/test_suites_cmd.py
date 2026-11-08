import argparse
import json
from pathlib import Path

import pytest

from quality.cli import suites_cmd
from quality.cli.main import main
from quality.engine import ProfileAdapter
from quality.policy import Profile
from quality.testing import Suite
from quality.testkit.gitrepo import GitRepo
from quality.testkit.policies import ComponentSpec, policy_toml
from quality.testkit.scenarios import ScenarioSpec, manifest_toml
from quality.testkit.suites import ScriptedRunner
from quality.toolchain import ToolchainError
from quality.toolchain.install import MISSING

COVERED = (
    "unit coverage of app: line 100.00% (2/2), branch 100.00% (1/1), "
    "changed lines not applicable (0/0)\n"
)


@pytest.fixture
def runner() -> ScriptedRunner:
    return ScriptedRunner({}, cover=True)


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: ScriptedRunner) -> GitRepo:
    def profiles() -> dict[Profile, ProfileAdapter]:
        return {Profile.PYTHON: runner}

    repo = GitRepo.create(tmp_path / "repo")
    repo.write("quality.toml", policy_toml())
    repo.write("README.md", "# Project\n")
    repo.write("src/app/__init__.py", "")
    repo.write("tests/test_app.py", "def test_ok():\n    pass\n")
    repo.git("add", "-A")
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(repo.home))
    monkeypatch.setattr(suites_cmd, "local_profiles", profiles)
    monkeypatch.chdir(repo.path)
    return repo


def ran(runner: ScriptedRunner) -> list[tuple[str, Suite]]:
    return [(request.component.id, request.suite) for request in runner.requests]


def test_the_affected_components_of_the_index_are_tested(
    project: GitRepo, runner: ScriptedRunner, capsys: pytest.CaptureFixture[str]
) -> None:
    project.write("src/app/unstaged.py", "def broken(:\n")
    assert main(["test"]) == 0
    assert capsys.readouterr().out == f"PASSED: 0 error(s), 0 warning(s)\n{COVERED}tested app\n"
    assert ran(runner) == [("app", Suite.UNIT)]


def test_unaffected_components_are_tested_only_on_request(
    project: GitRepo, runner: ScriptedRunner, capsys: pytest.CaptureFixture[str]
) -> None:
    project.git("commit", "-q", "-m", "first")
    assert main(["test"]) == 0
    assert capsys.readouterr().out.endswith("tested no components\n")
    assert main(["test", "--rev", "HEAD", "--all", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert (report["command"], report["tested"]) == ("test", ["app"])
    (coverage,) = report["coverage"]
    assert (coverage["component"], coverage["line"]) == ("app", {"covered": 2, "total": 2})
    assert main(["test", "--component", "app"]) == 0
    assert ran(runner) == [("app", Suite.UNIT)] * 2


def test_requested_suites_must_exist(
    project: GitRepo, runner: ScriptedRunner, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["test", "--suite", "integration", "--component", "ghost"]) == 2
    output = capsys.readouterr().out
    assert "'ghost' is not an active component (tests.unknown-component)" in output
    assert main(["test", "--suite", "integration"]) == 2
    output = capsys.readouterr().out
    assert "no integration suite is declared by app (tests.undeclared)" in output
    assert output.endswith("tested no components\n")
    assert runner.requests == []


def test_suites_run_in_a_stable_order() -> None:
    assert suites_cmd.suites_of(argparse.Namespace(suite=None)) == (Suite.UNIT,)
    chosen = argparse.Namespace(suite=["acceptance", "unit", "integration"])
    assert suites_cmd.suites_of(chosen) == (Suite.UNIT, Suite.INTEGRATION, Suite.ACCEPTANCE)


def test_missing_tools_are_environment_errors(
    project: GitRepo, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def missing() -> object:
        raise ToolchainError(MISSING.diagnostic("ruff is not installed"))

    monkeypatch.setattr(suites_cmd, "local_profiles", missing)
    assert main(["test", "--json"]) == 3
    report = json.loads(capsys.readouterr().out)
    assert [d["rule"] for d in report["diagnostics"]] == ["tools.missing"]
    assert (report["tested"], report["coverage"]) == ([], [])


def test_outside_a_repository_is_an_environment_error(
    project: GitRepo,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.chdir(outside)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    assert main(["test"]) == 3
    assert "(git.failed)" in capsys.readouterr().out


def test_the_scenarios_of_the_suites_that_ran_are_proved(
    project: GitRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    spec = ComponentSpec(capabilities=("cli",), e2e=("e2e",), scenarios="scenarios.toml")
    smoke = ScenarioSpec("smoke", ("tests::test_ok",), "cli", "e2e", requirements=("cli.smoke",))
    flows = ScenarioSpec("flows", ("tests::test_ok",), "cli", "e2e", requirements=("cli.flows",))
    project.write("quality.toml", policy_toml(spec))
    project.write("scenarios.toml", manifest_toml(smoke, flows))
    project.write("e2e/test_smoke.py", "def test_ok():\n    pass\n")
    project.git("add", "-A")
    assert main(["test", "--suite", "e2e"]) == 0
    assert capsys.readouterr().out == (
        "PASSED: 0 error(s), 0 warning(s)\nscenarios of app: 2 of 2 proved\ntested app\n"
    )
    assert main(["test", "--suite", "e2e", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["scenarios"] == [
        {
            "component": "app",
            "required": 2,
            "proved": 2,
            "suspended": 0,
            "deferred": 0,
            "boundaries": [],
        }
    ]
