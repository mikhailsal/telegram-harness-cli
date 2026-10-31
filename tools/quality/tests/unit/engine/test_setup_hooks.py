from pathlib import Path

from quality.diagnostics import Severity
from quality.engine.setup import SetupReport, hook_status, install_hooks
from quality.gitio import Repository
from quality.hooks import HookName, script
from quality.testkit.gitrepo import GitRepo

FOREIGN = b"#!/bin/sh\necho my own hook\n"


def hook(repo: GitRepo, name: HookName = HookName.PRE_COMMIT) -> Path:
    return repo.path / ".git" / "hooks" / str(name)


def rules_of(report: SetupReport) -> list[str]:
    return [d.rule for d in report.diagnostics]


def test_hooks_are_installed_and_reported_current(repo: GitRepo) -> None:
    git = repo.repository()
    report = install_hooks(git, HookName)
    assert report == SetupReport([], [f"{name} hook: {hook(repo, name)}" for name in HookName])
    assert hook(repo).read_text() == script(HookName.PRE_COMMIT)
    assert install_hooks(git, [HookName.PRE_COMMIT]).lines == [f"pre-commit hook: {hook(repo)}"]
    status = hook_status(git, frozenset(HookName))
    assert status == SetupReport([], ["pre-commit hook: current", "pre-push hook: current"])


def test_a_foreign_hook_is_kept_and_reported(repo: GitRepo) -> None:
    hook(repo).write_bytes(FOREIGN)
    report = install_hooks(repo.repository(), HookName)
    assert rules_of(report) == ["hooks.conflict"]
    assert report.lines == [f"pre-push hook: {hook(repo, HookName.PRE_PUSH)}"]
    assert hook(repo).read_bytes() == FOREIGN


def test_hook_status_reports_missing_and_foreign_hooks(repo: GitRepo) -> None:
    hook(repo).write_bytes(FOREIGN)
    report = hook_status(repo.repository(), frozenset(HookName))
    assert rules_of(report) == ["hooks.conflict", "hooks.missing"]
    assert report.lines == ["pre-commit hook: foreign", "pre-push hook: missing"]


def test_an_optional_outdated_hook_is_a_warning(repo: GitRepo) -> None:
    git = repo.repository()
    install_hooks(git, [HookName.PRE_PUSH])
    hook(repo, HookName.PRE_PUSH).chmod(0o644)
    report = hook_status(git, frozenset({HookName.PRE_COMMIT}))
    assert [(d.rule, d.severity) for d in report.diagnostics] == [
        ("hooks.missing", Severity.ERROR),
        ("hooks.missing", Severity.WARNING),
    ]
    assert report.lines == ["pre-commit hook: missing", "pre-push hook: outdated"]


def test_a_hooks_path_is_a_conflict(repo: GitRepo) -> None:
    repo.git("config", "core.hooksPath", ".githooks")
    git = repo.repository()
    installed = install_hooks(git, HookName)
    assert (rules_of(installed), installed.lines) == (["hooks.conflict"] * 2, [])
    status = hook_status(git, frozenset(HookName))
    assert (rules_of(status), status.lines) == (["hooks.conflict"], [])
    assert not (repo.path / ".githooks").exists()


def test_an_unreadable_git_config_is_a_git_failure(repo: GitRepo) -> None:
    git = Repository(repo.path, repo.path / ".git", repo.environ())
    (repo.path / ".git" / "config").write_text("[core\n")
    installed = install_hooks(git, [HookName.PRE_COMMIT])
    assert (rules_of(installed), installed.lines) == (["git.failed"], [])
    status = hook_status(git, frozenset(HookName))
    assert (rules_of(status), status.lines) == (["git.failed"], [])
