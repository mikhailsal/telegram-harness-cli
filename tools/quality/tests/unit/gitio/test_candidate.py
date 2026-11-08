from pathlib import Path

import pytest

from quality.gitio import GitioError, index_tree, list_tree, revision, staged
from quality.testkit.gitrepo import GitRepo


def tree_paths(repo: GitRepo, tree: str) -> dict[str, str]:
    return {e.path: e.oid for e in list_tree(repo.repository(), tree)}


def blob(repo: GitRepo, text: str) -> str:
    return repo.git("hash-object", "--stdin", stdin=text.encode()).strip()


def test_first_commit_compares_with_the_empty_tree(repo: GitRepo) -> None:
    repo.write("a.py", "print(1)\n")
    repo.git("add", "a.py")
    candidate = staged(repo.repository())
    assert candidate.base_tree == repo.repository().empty_tree()
    assert tree_paths(repo, candidate.tree) == {"a.py": blob(repo, "print(1)\n")}


def test_staged_content_wins_over_work_tree_and_untracked_files(repo: GitRepo) -> None:
    repo.write("a.py", "old\n")
    repo.commit_all("first")
    repo.write("a.py", "staged\n")
    repo.git("add", "a.py")
    repo.write("a.py", "unstaged\n")
    repo.write("untracked.py", "x\n")
    repo.write("new.py", "intent\n")
    repo.git("add", "-N", "new.py")
    candidate = staged(repo.repository())
    assert tree_paths(repo, candidate.tree) == {"a.py": blob(repo, "staged\n")}
    assert candidate.base_tree == repo.git("rev-parse", "HEAD^{tree}").strip()


def test_staged_deletions_and_renames_are_part_of_the_candidate(repo: GitRepo) -> None:
    repo.write("gone.py", "x\n")
    repo.write("old.py", "y\n")
    repo.commit_all("first")
    repo.git("rm", "-q", "gone.py")
    repo.git("mv", "old.py", "new.py")
    assert set(tree_paths(repo, staged(repo.repository()).tree)) == {"new.py"}


def test_the_original_index_is_not_modified(repo: GitRepo) -> None:
    repo.write("a.py", "a\n")
    repo.git("add", "a.py")
    index = repo.path / ".git" / "index"
    before = (index.read_bytes(), index.stat().st_mtime_ns)
    staged(repo.repository()).verify_unchanged()
    assert (index.read_bytes(), index.stat().st_mtime_ns) == before


def test_alternate_index_is_used_deliberately(repo: GitRepo, tmp_path: Path) -> None:
    repo.write("a.py", "default\n")
    repo.git("add", "a.py")
    alternate = tmp_path / "alternate-index"
    repo.write("b.py", "alternate\n")
    repo.git("add", "b.py", GIT_INDEX_FILE=str(alternate))
    candidate = staged(repo.repository(), alternate)
    assert set(tree_paths(repo, candidate.tree)) == {"b.py"}
    assert str(alternate) in candidate.description


def test_missing_index_is_an_empty_candidate(repo: GitRepo, tmp_path: Path) -> None:
    git = repo.repository()
    assert index_tree(git, tmp_path / "missing-index") == git.empty_tree()


def test_split_index_is_supported(repo: GitRepo) -> None:
    repo.git("config", "core.splitIndex", "true")
    repo.write("a.py", "a\n")
    repo.git("add", "a.py")
    repo.write("b.py", "b\n")
    repo.git("add", "b.py")
    assert set(tree_paths(repo, staged(repo.repository()).tree)) == {"a.py", "b.py"}


def test_concurrent_index_changes_are_detected(repo: GitRepo) -> None:
    repo.write("a.py", "a\n")
    repo.git("add", "a.py")
    candidate = staged(repo.repository())
    candidate.verify_unchanged()
    repo.write("a.py", "changed\n")
    repo.git("add", "a.py")
    with pytest.raises(GitioError) as raised:
        candidate.verify_unchanged()
    assert raised.value.diagnostic.rule == "git.candidate-changed"


def conflict(repo: GitRepo) -> None:
    repo.write("a.py", "base\n")
    repo.commit_all("base")
    repo.git("checkout", "-q", "-b", "topic")
    repo.write("a.py", "topic\n")
    repo.commit_all("topic")
    repo.git("checkout", "-q", "main")
    repo.write("a.py", "main\n")
    repo.commit_all("main")
    assert not repo.run_git("merge", "-q", "topic").ok


def test_unresolved_merges_are_rejected(repo: GitRepo) -> None:
    conflict(repo)
    with pytest.raises(GitioError) as raised:
        staged(repo.repository())
    diagnostic = raised.value.diagnostic
    assert diagnostic.rule == "git.unmerged-index"
    assert diagnostic.location is not None
    assert diagnostic.location.path == "a.py"


def test_resolved_merge_commits_compare_with_the_first_parent(repo: GitRepo) -> None:
    conflict(repo)
    repo.write("a.py", "resolved\n")
    repo.git("add", "a.py")
    candidate = staged(repo.repository())
    assert candidate.base_tree == repo.git("rev-parse", "HEAD^{tree}").strip()
    assert tree_paths(repo, candidate.tree) == {"a.py": blob(repo, "resolved\n")}


def test_amend_compares_with_the_commit_being_replaced(repo: GitRepo) -> None:
    repo.write("a.py", "one\n")
    repo.commit_all("first")
    repo.write("a.py", "two\n")
    second = repo.commit_all("second")
    repo.write("a.py", "amended\n")
    repo.git("add", "a.py")
    candidate = staged(repo.repository())
    assert candidate.base_tree == repo.git("rev-parse", f"{second}^{{tree}}").strip()


def test_revision_candidates_compare_with_the_first_parent(repo: GitRepo) -> None:
    repo.write("a.py", "one\n")
    first = repo.commit_all("first")
    repo.write("a.py", "two\n")
    second = repo.commit_all("second")
    git = repo.repository()
    root = revision(git, first)
    assert root.base_tree == git.empty_tree()
    tip = revision(git, "main")
    assert tip.tree == git.tree_of(second)
    assert tip.base_tree == git.tree_of(first)
    tip.verify_unchanged()
    repo.write("a.py", "three\n")
    repo.commit_all("third")
    with pytest.raises(GitioError, match="revision main changed"):
        tip.verify_unchanged()


def test_detached_head_is_supported(repo: GitRepo) -> None:
    repo.write("a.py", "one\n")
    first = repo.commit_all("first")
    repo.commit_all("second")
    repo.git("checkout", "-q", "--detach", first)
    repo.write("a.py", "detached\n")
    repo.git("add", "a.py")
    candidate = staged(repo.repository())
    assert candidate.base_tree == repo.repository().tree_of(first)


def test_fsmonitor_hooks_are_not_run(repo: GitRepo, tmp_path: Path) -> None:
    marker = tmp_path / "fsmonitor-ran"
    hook = repo.write("hook.sh", f"#!/bin/sh\ntouch {marker}\n", executable=True)
    repo.git("add", "hook.sh")
    repo.git("config", "core.fsmonitor", str(hook))
    staged(repo.repository())
    assert not marker.exists()
