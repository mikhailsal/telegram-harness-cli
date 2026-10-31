from pathlib import Path

import pytest

from quality.gitio import GitioError, Repository, create_repository
from quality.testkit.gitrepo import IDENTITY, GitRepo


def rule_of(error: pytest.ExceptionInfo[GitioError]) -> str:
    return error.value.diagnostic.rule


def test_discover_from_a_subdirectory(repo: GitRepo) -> None:
    (repo.path / "sub" / "dir").mkdir(parents=True)
    found = Repository.discover(repo.path / "sub" / "dir", repo.environ())
    assert found.worktree == repo.path
    assert found.git_dir == repo.path / ".git"
    assert found.env["GIT_DIR"] == str(repo.path / ".git")
    assert found.env["GIT_WORK_TREE"] == str(repo.path)


def test_discover_outside_a_repository_fails(tmp_path: Path) -> None:
    outside = tmp_path / "plain"
    outside.mkdir()
    with pytest.raises(GitioError) as raised:
        Repository.discover(outside, {"HOME": str(tmp_path)})
    assert rule_of(raised) == "git.failed"


def test_explicit_git_dir_is_honored_and_other_git_variables_are_dropped(
    repo: GitRepo, tmp_path: Path
) -> None:
    other = GitRepo.create(tmp_path / "other")
    environ = repo.environ(
        GIT_DIR=str(other.path / ".git"),
        GIT_WORK_TREE=str(other.path),
        GIT_INDEX_FILE=str(tmp_path / "foreign-index"),
        GIT_OBJECT_DIRECTORY=str(tmp_path / "objects"),
    )
    found = Repository.discover(repo.path, environ)
    assert found.worktree == other.path
    assert "GIT_INDEX_FILE" not in found.env
    assert "GIT_OBJECT_DIRECTORY" not in found.env


def test_revisions_resolve_to_full_ids(repo: GitRepo) -> None:
    repo.write("a.txt", "a\n")
    commit = repo.commit_all("first")
    git = repo.repository()
    assert git.resolve_commit("main") == commit
    assert git.resolve_commit("HEAD") == commit
    assert git.head_commit() == commit
    assert git.tree_of(commit) == repo.git("rev-parse", "HEAD^{tree}").strip()


@pytest.mark.parametrize("name", ["", "--output=/x", "no-such-branch", "0" * 40])
def test_unknown_or_option_like_revisions_fail(repo: GitRepo, name: str) -> None:
    repo.commit_all("first")
    with pytest.raises(GitioError) as raised:
        repo.repository().resolve_commit(name)
    assert rule_of(raised) == "git.revision-unresolvable"


def test_unborn_head_has_no_commit(repo: GitRepo) -> None:
    git = repo.repository()
    assert git.head_commit() is None
    assert git.empty_tree() == "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
    assert git.default_index() == repo.path / ".git" / "index"


def test_merge_base_and_ancestry(repo: GitRepo) -> None:
    root = repo.commit_all("root")
    repo.git("checkout", "-q", "-b", "topic")
    topic = repo.commit_all("topic")
    git = repo.repository()
    assert git.merge_base(root, topic) == root
    assert git.is_ancestor(root, topic)
    assert not git.is_ancestor(topic, root)
    repo.git("checkout", "-q", "--orphan", "unrelated")
    unrelated = repo.commit_all("unrelated")
    with pytest.raises(GitioError) as raised:
        git.merge_base(topic, unrelated)
    assert rule_of(raised) == "git.revision-unresolvable"


def test_failing_git_commands_raise(repo: GitRepo) -> None:
    with pytest.raises(GitioError, match="git cat-file failed") as raised:
        repo.repository().git("cat-file", "-p", "f" * 40)
    assert rule_of(raised) == "git.failed"


def test_oversized_output_fails_instead_of_truncating(repo: GitRepo) -> None:
    repo.write("a.txt", "x" * 100)
    repo.commit_all("first")
    with pytest.raises(GitioError, match="output exceeded 10 bytes"):
        repo.repository().git("cat-file", "-p", "HEAD:a.txt", output_limit=10)


def test_missing_git_executable_is_reported(repo: GitRepo, tmp_path: Path) -> None:
    git = repo.repository()
    broken = Repository(git.worktree, git.git_dir, {**git.env, "PATH": str(tmp_path)})
    with pytest.raises(GitioError, match="executable not found") as raised:
        broken.git("status")
    assert rule_of(raised) == "git.failed"


def test_a_new_repository_commits_every_file_as_the_user(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / "build").mkdir(parents=True)
    (project / ".gitignore").write_text("build/\n")
    (project / "build" / "kept.txt").write_text("kept\n")
    (project / "README.md").write_text("# Project\n")
    foreign = GitRepo.create(tmp_path / "foreign")
    environ = {
        **IDENTITY,
        "HOME": str(foreign.home),
        "GIT_DIR": str(foreign.path / ".git"),
        "GIT_INDEX_FILE": str(tmp_path / "foreign-index"),
    }
    created = create_repository(project, branch="trunk", message="Root\n", environ=environ)
    assert created.worktree == project
    log = created.git("log", "--format=%an <%ae>%n%B", "trunk").stdout_text()
    assert log == "Quality Test <quality-test@example.invalid>\nRoot\n\n"
    listed = created.git("ls-files").stdout_text().split()
    assert listed == [".gitignore", "README.md", "build/kept.txt"]
    assert created.git("status", "--porcelain").stdout_text() == ""
    assert foreign.run_git("rev-parse", "--verify", "--quiet", "HEAD").returncode != 0


def test_a_new_repository_needs_the_user_s_identity(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "README.md").write_text("# Project\n")
    home = tmp_path / "home"
    home.mkdir()
    with pytest.raises(GitioError, match="git commit failed") as raised:
        create_repository(project, branch="main", message="Root\n", environ={"HOME": str(home)})
    assert rule_of(raised) == "git.failed"


def test_a_new_repository_needs_a_valid_branch_name(tmp_path: Path) -> None:
    with pytest.raises(GitioError, match="git init failed"):
        create_repository(tmp_path, branch="a..b", message="Root\n", environ=IDENTITY)
