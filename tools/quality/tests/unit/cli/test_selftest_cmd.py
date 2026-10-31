import json
from pathlib import Path

import pytest

from quality.cli import selftest_cmd
from quality.cli.main import main
from quality.engine import ProfileAdapter
from quality.policy import Profile
from quality.testing import Suite
from quality.testkit.gitrepo import GitRepo
from quality.testkit.policies import ComponentSpec, policy_toml
from quality.testkit.suites import ScriptedRunner

APP = ComponentSpec(id="app", root="app")
TOOL = ComponentSpec(id="tool", root="tool", role="tooling", integration=("acceptance",))
# The spec renders only unit and integration roots; declare the tool's roots as acceptance.
POLICY = policy_toml(APP, TOOL).replace(
    'integration = ["acceptance"]', 'acceptance = ["acceptance"]'
)
COVERED = (
    "unit coverage of tool: line 100.00% (2/2), branch 100.00% (1/1), "
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
    repo.write("quality.toml", POLICY)
    repo.write("README.md", "# Project\n")
    for name in ("app", "tool"):
        repo.write(f"{name}/src/{name}.py", "def f():\n    return 1\n")
        repo.write(f"{name}/tests/test_{name}.py", "def test_f():\n    assert True\n")
    repo.write("tool/acceptance/test_flow.py", "def test_flow():\n    assert True\n")
    repo.commit_all("base")
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(repo.home))
    monkeypatch.setattr(selftest_cmd, "local_profiles", profiles)
    monkeypatch.chdir(repo.path)
    return repo


def ran(runner: ScriptedRunner) -> list[tuple[str, Suite]]:
    return [(request.component.id, request.suite) for request in runner.requests]


def test_the_unit_suites_of_tooling_components_run_unaffected(
    project: GitRepo, runner: ScriptedRunner, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["self-test"]) == 0
    assert capsys.readouterr().out == f"PASSED: 0 error(s), 0 warning(s)\n{COVERED}tested tool\n"
    assert ran(runner) == [("tool", Suite.UNIT)]


def test_the_acceptance_level_of_a_revision_as_json(
    project: GitRepo, runner: ScriptedRunner, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["self-test", "--level", "acceptance", "--rev", "HEAD", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert (report["command"], report["tested"], report["coverage"]) == ("self-test", ["tool"], [])
    assert ran(runner) == [("tool", Suite.ACCEPTANCE)]


def test_an_undeclared_level_is_a_usage_error(
    project: GitRepo, runner: ScriptedRunner, capsys: pytest.CaptureFixture[str]
) -> None:
    project.write("quality.toml", policy_toml(APP, TOOL))
    project.git("add", "quality.toml")
    assert main(["self-test", "--level", "acceptance"]) == 2
    output = capsys.readouterr().out
    assert "no acceptance suite is declared by tool (tests.undeclared)" in output
    assert runner.requests == []


def test_without_a_tooling_component_nothing_runs(
    project: GitRepo, runner: ScriptedRunner, capsys: pytest.CaptureFixture[str]
) -> None:
    project.write("quality.toml", policy_toml(APP))
    project.git("add", "quality.toml")
    assert main(["self-test"]) == 2
    output = capsys.readouterr().out
    assert "no active component has the tooling role (tests.unknown-component)" in output
    assert output.endswith("tested no components\n")
