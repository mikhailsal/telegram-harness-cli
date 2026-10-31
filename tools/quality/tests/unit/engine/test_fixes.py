import os
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from quality.adapters import native
from quality.catalog import build_registry
from quality.diagnostics import Diagnostic, Location
from quality.engine.fixes import FixReport, fix_worktree, worktree_changes, worktree_entries
from quality.gitio import TreeEntry
from quality.policy import Component, Profile
from quality.testkit.gitrepo import GitRepo
from quality.testkit.policies import ComponentSpec, policy_toml

RULES = tuple(rule.id for rule in build_registry())
APP = ComponentSpec(root="app")
WEB = ComponentSpec(id="web", root="web", profile="typescript")
HELPER = ComponentSpec(id="helper", root="helper", profile="dart", role="helper")
LEFT = native.FAILED.diagnostic("ruff: app/src/app/core.py was left unformatted")


@dataclass
class Recorder:
    """A fixer that records its calls and returns or raises what it was given."""

    remaining: list[Diagnostic] = field(default_factory=list[Diagnostic])
    error: native.ToolFailedError | None = None
    calls: list[tuple[str, Path, tuple[str, ...]]] = field(
        default_factory=list[tuple[str, Path, tuple[str, ...]]]
    )

    def fix(self, component: Component, root: Path, files: tuple[str, ...]) -> list[Diagnostic]:
        self.calls.append((component.id, root, files))
        if self.error is not None:
            raise self.error
        return self.remaining


@pytest.fixture
def project(repo: GitRepo) -> GitRepo:
    repo.write("quality.toml", policy_toml(APP, WEB))
    repo.write(".gitignore", "build/\n")
    repo.write("README.md", "# Project\n")
    repo.write("app/pyproject.toml", '[project]\nname = "app"\n')
    repo.write("app/src/app/core.py", "x = 1\n")
    repo.write("app/tests/test_core.py", "def test_x() -> None:\n    pass\n")
    repo.write("web/src/index.ts", "export const x = 1;\n")
    repo.write("web/tests/index.test.ts", "")
    repo.commit_all("base")
    return repo


def test_worktree_entries_are_regular_files_that_are_not_ignored(repo: GitRepo) -> None:
    repo.write(".gitignore", "build/\n")
    repo.write("a.txt", "a")
    repo.write("run.sh", "#!/bin/sh\n", executable=True)
    repo.write("gone.txt", "gone")
    (repo.path / "link").symlink_to("a.txt")
    repo.commit_all("base")
    (repo.path / "gone.txt").unlink()
    repo.write("new.py", "y = 2\n")
    repo.write(b"bad\xff.txt", "b")
    repo.write("build/out.py", "z = 3\n")
    GitRepo.create(repo.path / "nested").write("f.txt", "f")
    assert worktree_entries(repo.repository()) == [
        TreeEntry("100644", "", 7, ".gitignore"),
        TreeEntry("100644", "", 1, "a.txt"),
        TreeEntry("100644", "", 1, os.fsdecode(b"bad\xff.txt")),
        TreeEntry("100644", "", 6, "new.py"),
        TreeEntry("100755", "", 10, "run.sh"),
    ]


def test_an_empty_worktree_has_no_entries(repo: GitRepo) -> None:
    assert worktree_entries(repo.repository()) == []


def test_unmerged_paths_are_listed_once(repo: GitRepo) -> None:
    repo.write("a.txt", "base\n")
    repo.commit_all("base")
    repo.git("checkout", "-q", "-b", "other")
    repo.write("a.txt", "other\n")
    repo.commit_all("other")
    repo.git("checkout", "-q", "main")
    repo.write("a.txt", "main\n")
    repo.commit_all("main")
    assert not repo.run_git("merge", "-q", "other").ok
    assert [entry.path for entry in worktree_entries(repo.repository())] == ["a.txt"]


def test_source_and_test_files_of_the_worktree_are_fixed(project: GitRepo) -> None:
    project.write("app/src/app/new.py", "y = 2\n")
    project.write("app/build/skip.py", "z = 3\n")
    (project.path / "app/tests/test_core.py").unlink()
    python, typescript = Recorder([LEFT]), Recorder()
    fixers = {Profile.PYTHON: python, Profile.TYPESCRIPT: typescript}
    report = fix_worktree(project.repository(), fixers, RULES)
    assert report == FixReport((LEFT,), ("app", "web"))
    assert python.calls == [("app", project.path, ("app/src/app/core.py", "app/src/app/new.py"))]
    assert typescript.calls == [
        ("web", project.path, ("web/src/index.ts", "web/tests/index.test.ts"))
    ]


def test_a_profile_without_a_fixer_is_reported(project: GitRepo) -> None:
    python = Recorder()
    report = fix_worktree(project.repository(), {Profile.PYTHON: python}, RULES)
    (problem,) = report.diagnostics
    assert (problem.rule, problem.component) == ("profiles.unavailable", "web")
    assert problem.message == "'web' uses the typescript profile; this build has no fixer for it"
    assert report.fixed == ("app",)
    assert [call[0] for call in python.calls] == ["app"]


def test_a_fixer_that_cannot_run_is_reported_and_not_counted(project: GitRepo) -> None:
    broken = Recorder(error=native.failure("ruff", "exit status 3"))
    fixers = {Profile.PYTHON: broken, Profile.TYPESCRIPT: Recorder()}
    report = fix_worktree(project.repository(), fixers, RULES)
    assert [(d.rule, d.message) for d in report.diagnostics] == [
        ("tools.failed", "ruff: exit status 3")
    ]
    assert report.fixed == ("web",)


def test_an_unusable_policy_stops_before_any_fixer(project: GitRepo) -> None:
    project.write("quality.toml", "[policy\n")
    python = Recorder()
    report = fix_worktree(project.repository(), {Profile.PYTHON: python}, RULES)
    assert report.fixed == ()
    assert [d.location for d in report.diagnostics] == [Location("quality.toml", 1, 8)]
    assert python.calls == []


def test_a_policy_that_fails_cross_validation_is_unusable(project: GitRepo) -> None:
    ghost = ComponentSpec(root="app", extra='depends_on = ["ghost"]')
    project.write("quality.toml", policy_toml(ghost, WEB))
    python = Recorder()
    report = fix_worktree(project.repository(), {Profile.PYTHON: python}, RULES)
    assert [(d.rule, d.component) for d in report.diagnostics] == [("policy.reference", "app")]
    assert (report.fixed, python.calls) == ((), [])


def test_worktree_changes_cover_the_index_the_working_tree_and_untracked_files(
    project: GitRepo,
) -> None:
    project.write("app/src/app/core.py", "x = 2\n")
    project.write("web/src/index.ts", "export const x = 2;\n")
    project.git("add", "web/src/index.ts")
    project.write("app/src/app/new.py", "y = 2\n")
    project.write("app/build/skip.py", "z = 3\n")
    assert worktree_changes(project.repository()) == [
        "app/src/app/core.py",
        "app/src/app/new.py",
        "web/src/index.ts",
    ]


def test_before_the_first_commit_every_file_is_a_change(repo: GitRepo) -> None:
    repo.write("a.txt", "a\n")
    repo.git("add", "a.txt")
    repo.write("b.txt", "b\n")
    assert worktree_changes(repo.repository()) == ["a.txt", "b.txt"]


@pytest.fixture
def with_helper(project: GitRepo) -> GitRepo:
    project.write("quality.toml", policy_toml(APP, WEB, HELPER))
    project.write("helper/pubspec.yaml", "name: helper\n")
    project.write("helper/lib/helper.dart", "void main() {}\n")
    project.commit_all("helper")
    return project


def test_an_unchanged_dormant_helper_is_not_fixed(with_helper: GitRepo) -> None:
    dart = Recorder()
    fixers = {Profile.PYTHON: Recorder(), Profile.TYPESCRIPT: Recorder(), Profile.DART: dart}
    report = fix_worktree(with_helper.repository(), fixers, RULES)
    assert report == FixReport((), ("app", "web"))
    assert dart.calls == []


def test_a_dormant_helper_without_a_fixer_is_not_reported(with_helper: GitRepo) -> None:
    fixers = {Profile.PYTHON: Recorder(), Profile.TYPESCRIPT: Recorder()}
    assert fix_worktree(with_helper.repository(), fixers, RULES) == FixReport((), ("app", "web"))


def test_a_helper_changed_in_the_working_tree_is_fixed(with_helper: GitRepo) -> None:
    with_helper.write("helper/lib/helper.dart", "void main() { }\n")
    dart = Recorder()
    fixers = {Profile.PYTHON: Recorder(), Profile.TYPESCRIPT: Recorder(), Profile.DART: dart}
    report = fix_worktree(with_helper.repository(), fixers, RULES)
    assert report.fixed == ("app", "web", "helper")
    assert [call[0] for call in dart.calls] == ["helper"]
