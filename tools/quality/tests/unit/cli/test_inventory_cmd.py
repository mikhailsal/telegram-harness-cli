import json
from pathlib import Path

import pytest

from quality.cli.inventory_cmd import index_from
from quality.cli.main import main
from quality.testkit.gitrepo import GitRepo
from quality.testkit.policies import ComponentSpec, policy_toml


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> GitRepo:
    repo = GitRepo.create(tmp_path / "repo")
    repo.write("quality.toml", policy_toml())
    repo.write("src/app/__init__.py", "")
    repo.write("tests/test_app.py", "def test_ok():\n    pass\n")
    repo.git("add", "-A")
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(repo.home))
    monkeypatch.chdir(repo.path)
    return repo


def test_lists_the_staged_candidate(project: GitRepo, capsys: pytest.CaptureFixture[str]) -> None:
    project.write("src/app/unstaged.go", "package main\n")
    assert main(["inventory"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "config    app              -          quality.toml",
        "source    app              python     src/app/__init__.py",
        "tests     app              python     tests/test_app.py",
        "PASSED: 0 error(s), 0 warning(s)",
    ]


def test_json_report_lists_files(project: GitRepo, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["inventory", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["command"] == "inventory"
    assert report["files"][1] == {
        "path": "src/app/__init__.py",
        "category": "source",
        "component": "app",
        "language": "python",
        "region": "source",
        "explicit": False,
    }


def test_discovery_violations_fail(project: GitRepo, capsys: pytest.CaptureFixture[str]) -> None:
    project.write("src/app/main.go", "package main\n")
    project.git("add", "-A")
    assert main(["inventory"]) == 1
    assert "go code has no supported profile (discovery.unsupported-language)" in (
        capsys.readouterr().out
    )


def test_a_revision_is_inspected_instead_of_the_index(
    project: GitRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    project.commit_all("good")
    project.write("src/app/main.go", "package main\n")
    project.git("add", "-A")
    assert main(["inventory", "--rev", "HEAD"]) == 0
    assert "main.go" not in capsys.readouterr().out


def test_the_callers_index_file_is_honored(
    project: GitRepo, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    project.write("src/app/main.go", "package main\n")
    project.git("add", "-A", GIT_INDEX_FILE=".git/other-index")
    monkeypatch.setenv("GIT_INDEX_FILE", ".git/other-index")
    assert main(["inventory"]) == 1
    assert "src/app/main.go" in capsys.readouterr().out


def test_index_file_is_resolved_against_the_working_directory(tmp_path: Path) -> None:
    assert index_from({}, tmp_path) is None
    assert index_from({"GIT_INDEX_FILE": "x/index"}, tmp_path) == tmp_path / "x/index"
    assert index_from({"GIT_INDEX_FILE": "/abs/index"}, tmp_path) == Path("/abs/index")


def test_policy_errors_are_usage_errors(
    project: GitRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    project.git("rm", "-q", "--cached", "quality.toml")
    assert main(["inventory"]) == 2
    assert "(policy.missing)" in capsys.readouterr().out
    project.write("quality.toml", policy_toml(ComponentSpec(extra='depends_on = ["ghost"]')))
    project.git("add", "quality.toml")
    assert main(["inventory"]) == 2
    assert "(policy.reference)" in capsys.readouterr().out


def test_git_errors_are_environment_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GIT_DIR", raising=False)
    assert main(["inventory"]) == 3
    assert "(git.failed)" in capsys.readouterr().out


def test_unusual_paths_are_escaped(project: GitRepo, capsys: pytest.CaptureFixture[str]) -> None:
    project.write(b"src/app/\x1b[31mred\nline\xff.md", "# x\n")
    project.git("add", "-A")
    assert main(["inventory"]) == 0
    assert "src/app/\\x1b[31mred\\x0aline\\xff.md" in capsys.readouterr().out
    assert main(["inventory", "--json"]) == 0
    paths = [f["path"] for f in json.loads(capsys.readouterr().out)["files"]]
    assert "src/app/\x1b[31mred\nline\udcff.md" in paths
