import json
from pathlib import Path

import pytest

from quality.cli.main import main
from quality.testkit.gitrepo import GitRepo
from quality.testkit.policies import ComponentSpec, policy_toml
from quality.testkit.scenarios import ScenarioSpec, manifest_toml

SPEC = ComponentSpec(capabilities=("cli",), e2e=("e2e",), scenarios="scenarios.toml")
SMOKE = ScenarioSpec("smoke", ("e2e::smoke",), "cli", "e2e", requirements=("cli.smoke",))
FLOWS = ScenarioSpec(
    "flows", ("e2e::flows*",), "cli", "e2e", stage="merge", requirements=("cli.flows",)
)


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> GitRepo:
    repo = GitRepo.create(tmp_path / "repo")
    repo.write("quality.toml", policy_toml(SPEC))
    repo.write("scenarios.toml", manifest_toml(SMOKE, FLOWS))
    repo.write("README.md", "# Project\n")
    repo.write("src/app/__init__.py", "")
    repo.write("tests/test_app.py", "def test_ok():\n    pass\n")
    repo.write("e2e/test_smoke.py", "def test_ok():\n    pass\n")
    repo.git("add", "-A")
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(repo.home))
    monkeypatch.chdir(repo.path)
    return repo


def test_the_scenarios_of_the_index_are_listed(
    project: GitRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    project.write("scenarios.toml", "not toml")
    assert main(["scenarios"]) == 0
    assert capsys.readouterr().out == (
        f"{'app':<16} {'smoke':<28} {'cli':<13} {'e2e':<11} {'commit':<6} e2e::smoke\n"
        f"{'app':<16} {'flows':<28} {'cli':<13} {'e2e':<11} {'merge':<6} e2e::flows*\n"
        "PASSED: 0 error(s), 0 warning(s)\n"
    )


def test_the_json_report_names_each_scenario(
    project: GitRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["scenarios", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["command"] == "scenarios"
    assert report["scenarios"][0] == {
        "component": "app",
        "manifest": "scenarios.toml",
        "id": "smoke",
        "capability": "cli",
        "level": "e2e",
        "stage": "commit",
        "requirements": ["cli.smoke"],
        "operations": [],
        "tests": ["e2e::smoke"],
    }
    assert [scenario["id"] for scenario in report["scenarios"]] == ["smoke", "flows"]


def test_undeclared_capabilities_are_suggested(
    project: GitRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    project.write("pyproject.toml", '[project]\nname = "app"\ndependencies = ["flask>=3"]\n')
    project.git("add", "-A")
    assert main(["scenarios"]) == 0
    out = capsys.readouterr().out
    assert (
        "info: 'app' may have the 'http-api' capability: it depends on 'flask' "
        "(scenarios.suggestion) [app]\n"
    ) in out
    assert out.endswith("PASSED: 0 error(s), 0 warning(s)\n")


def test_an_uncovered_requirement_fails(
    project: GitRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    project.write("scenarios.toml", manifest_toml(SMOKE))
    project.git("add", "-A")
    assert main(["scenarios"]) == 1
    out = capsys.readouterr().out
    assert "(scenarios.uncovered) [app]" in out
    assert out.endswith("FAILED: 1 error(s), 0 warning(s)\n")


def test_a_revision_is_inspected_instead_of_the_index(
    project: GitRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    project.commit_all("start")
    project.write("scenarios.toml", manifest_toml(SMOKE))
    project.git("add", "-A")
    assert main(["scenarios", "--rev", "HEAD"]) == 0
    assert "flows" in capsys.readouterr().out


def test_an_invalid_policy_lists_nothing(
    project: GitRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    project.write("quality.toml", "schema_version = 1\n")
    project.git("add", "-A")
    assert main(["scenarios", "--json"]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["scenarios"] == []
    assert report["diagnostics"]


def test_a_git_error_is_reported(project: GitRepo, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["scenarios", "--rev", "no-such-revision"]) == 3
    assert capsys.readouterr().out.endswith("FAILED: 1 error(s), 0 warning(s)\n")
