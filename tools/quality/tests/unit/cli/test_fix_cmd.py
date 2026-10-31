import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from quality.adapters.python.ruff import LINT
from quality.cli import fix_cmd
from quality.cli.main import main
from quality.diagnostics import Diagnostic
from quality.policy import Component, Profile
from quality.testkit.gitrepo import GitRepo
from quality.testkit.policies import policy_toml
from quality.toolchain import ToolchainError
from quality.toolchain.install import MISSING

FILES = ("src/app/__init__.py", "src/app/new.py", "tests/test_app.py")


@dataclass(slots=True)
class ScriptedFixer:
    """Fixes nothing; records each call and returns the scripted findings."""

    remaining: list[Diagnostic] = field(default_factory=list[Diagnostic])
    calls: list[tuple[str, Path, tuple[str, ...]]] = field(
        default_factory=list[tuple[str, Path, tuple[str, ...]]]
    )

    def fix(self, component: Component, root: Path, files: tuple[str, ...]) -> list[Diagnostic]:
        self.calls.append((component.id, root, files))
        return list(self.remaining)


@pytest.fixture
def fixer() -> ScriptedFixer:
    return ScriptedFixer()


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fixer: ScriptedFixer) -> GitRepo:
    repo = GitRepo.create(tmp_path / "repo")
    repo.write("quality.toml", policy_toml())
    repo.write("README.md", "# Project\n")
    repo.write("src/app/__init__.py", "")
    repo.write("tests/test_app.py", "def test_ok():\n    pass\n")
    repo.git("add", "-A")
    repo.write("src/app/new.py", "x = 1\n")
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(repo.home))
    monkeypatch.setattr(fix_cmd, "local_fixers", lambda: {Profile.PYTHON: fixer})
    monkeypatch.chdir(repo.path)
    return repo


def test_working_tree_files_are_fixed_and_nothing_is_staged(
    project: GitRepo, fixer: ScriptedFixer, capsys: pytest.CaptureFixture[str]
) -> None:
    staged = project.git("ls-files", "--stage")
    assert main(["fix"]) == 0
    assert capsys.readouterr().out == (
        "PASSED: 0 error(s), 0 warning(s)\nfixed app; nothing was staged\n"
    )
    assert fixer.calls == [("app", project.path, FILES)]
    assert project.git("ls-files", "--stage") == staged


def test_remaining_findings_fail_with_a_json_report(
    project: GitRepo, fixer: ScriptedFixer, capsys: pytest.CaptureFixture[str]
) -> None:
    fixer.remaining = [LINT.diagnostic("E741: ambiguous variable name", component="app")]
    assert main(["fix", "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert (report["command"], report["fixed"]) == ("fix", ["app"])
    assert [d["rule"] for d in report["diagnostics"]] == ["python.lint"]


def test_a_missing_fixer_tool_is_an_environment_error(
    project: GitRepo, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def missing() -> object:
        raise ToolchainError(MISSING.diagnostic("ruff is not installed"))

    monkeypatch.setattr(fix_cmd, "local_fixers", missing)
    assert main(["fix"]) == 3
    output = capsys.readouterr().out
    assert "ruff is not installed (tools.missing)" in output
    assert output.endswith("fixed no components; nothing was staged\n")


def test_outside_a_repository_is_an_environment_error(
    project: GitRepo,
    fixer: ScriptedFixer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.chdir(outside)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    assert main(["fix", "--json"]) == 3
    report = json.loads(capsys.readouterr().out)
    assert ([d["rule"] for d in report["diagnostics"]], report["fixed"]) == (["git.failed"], [])
    assert fixer.calls == []
