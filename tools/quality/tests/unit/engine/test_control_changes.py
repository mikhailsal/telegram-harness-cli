from datetime import date
from pathlib import Path

import pytest

from quality.catalog import build_registry
from quality.cli.main import command_table
from quality.engine import Report, Settings, check_candidate
from quality.gitio import staged
from quality.policy import Profile
from quality.policy.scenarios import Stage
from quality.testkit.candidates import pinned_tools
from quality.testkit.gitrepo import GitRepo
from quality.testkit.policies import policy_toml
from quality.testkit.suites import ScriptedRunner

TODAY = date(2026, 10, 21)
CI = '[ci]\nworkflow = ".github/workflows/quality.yml"\nrequired_jobs = ["gate"]\n'


@pytest.fixture
def project(tmp_path: Path) -> GitRepo:
    repo = GitRepo.create(tmp_path / "repo")
    repo.write("quality.toml", policy_toml(extra=CI))
    repo.write("README.md", "# Project\n\nSee [agent instructions](AGENTS.md).\n")
    repo.write("AGENTS.md", "# Agents\n\nRun `quality check` before committing.\n")
    repo.write("src/app/__init__.py", "")
    repo.write("tests/test_core.py", "def test_add():\n    assert 1 + 1 == 2\n")
    repo.commit_all("base")
    return repo


def check(repo: GitRepo, stage: Stage) -> Report:
    git = repo.repository()
    settings = Settings(
        tools=pinned_tools(),
        commands=command_table(),
        rules={rule.id: rule for rule in build_registry()},
        today=TODAY,
        profiles={Profile.PYTHON: ScriptedRunner({}, cover=True)},
        stage=stage,
    )
    return check_candidate(git, staged(git), settings)


def changed_control(report: Report) -> list[str]:
    return [d.message for d in report.diagnostics if d.rule == "ci.control-changed"]


def test_the_merge_stage_lists_changed_and_deleted_control_files(project: GitRepo) -> None:
    project.write("quality.toml", policy_toml(extra=CI) + "\n")
    project.write("src/app/core.py", "x = 1\n")
    project.git("rm", "-q", "AGENTS.md")
    project.write(".gitattributes", "* text=auto\n")
    project.git("add", "-A")
    assert changed_control(check(project, Stage.MERGE)) == [
        "control file '.gitattributes' changed; it needs code-owner review",
        "control file 'quality.toml' changed; it needs code-owner review",
    ]


def test_the_commit_stage_does_not_list_control_changes(project: GitRepo) -> None:
    project.write(".gitattributes", "* text=auto\n")
    project.git("add", "-A")
    assert changed_control(check(project, Stage.COMMIT)) == []


def test_a_policy_without_ci_lists_no_control_changes(project: GitRepo) -> None:
    project.write("quality.toml", policy_toml())
    project.git("add", "-A")
    assert changed_control(check(project, Stage.MERGE)) == []
