import errno
from pathlib import Path

import pytest

from quality.gitio import GitioError, Repository
from quality.hooks import (
    HookConflictError,
    HookName,
    HookStatus,
    hooks_directory,
    inspect,
    install,
    script,
)
from quality.testkit.gitrepo import GitRepo

FOREIGN = b"#!/bin/sh\necho my own hook\n"


def hook_path(repo: GitRepo, name: HookName = HookName.PRE_COMMIT) -> Path:
    return repo.path / ".git" / "hooks" / str(name)


@pytest.mark.parametrize("name", list(HookName))
def test_a_fresh_install_is_current_and_executable(repo: GitRepo, name: HookName) -> None:
    state = install(repo.repository(), name)
    assert (state.name, state.path, state.status) == (name, hook_path(repo, name), "current")
    assert state.path.read_text() == script(name)
    assert state.path.stat().st_mode & 0o777 == 0o755
    assert inspect(repo.repository(), name) == state


def test_a_second_install_changes_nothing(repo: GitRepo) -> None:
    first = install(repo.repository(), HookName.PRE_COMMIT)
    before = (first.path.read_bytes(), first.path.stat().st_mtime_ns, first.path.stat().st_ino)
    second = install(repo.repository(), HookName.PRE_COMMIT)
    after = (second.path.read_bytes(), second.path.stat().st_mtime_ns, second.path.stat().st_ino)
    assert (second.status, after) == (HookStatus.CURRENT, before)
    assert not [path for path in first.path.parent.iterdir() if path.name.startswith(".")]


def test_a_missing_hooks_directory_is_created(repo: GitRepo) -> None:
    directory = repo.path / ".git" / "hooks"
    for sample in directory.iterdir():
        sample.unlink()
    directory.rmdir()
    assert inspect(repo.repository(), HookName.PRE_COMMIT).status is HookStatus.MISSING
    assert install(repo.repository(), HookName.PRE_COMMIT).status is HookStatus.CURRENT


def test_an_older_managed_hook_is_replaced(repo: GitRepo) -> None:
    path = hook_path(repo)
    path.write_text("#!/bin/sh\n# quality-managed-hook: pre-commit v0\nexit 0\n")
    path.chmod(0o755)
    assert inspect(repo.repository(), HookName.PRE_COMMIT).status is HookStatus.OUTDATED
    assert install(repo.repository(), HookName.PRE_COMMIT).status is HookStatus.CURRENT
    assert path.read_text() == script(HookName.PRE_COMMIT)


def test_a_managed_hook_that_is_not_executable_is_outdated(repo: GitRepo) -> None:
    path = install(repo.repository(), HookName.PRE_COMMIT).path
    path.chmod(0o644)
    assert inspect(repo.repository(), HookName.PRE_COMMIT).status is HookStatus.OUTDATED
    install(repo.repository(), HookName.PRE_COMMIT)
    assert path.stat().st_mode & 0o777 == 0o755


def assert_conflict(repo: Repository, name: HookName = HookName.PRE_COMMIT) -> str:
    with pytest.raises(HookConflictError) as raised:
        install(repo, name)
    diagnostic = raised.value.diagnostic
    assert (diagnostic.rule, diagnostic.kind) == ("hooks.conflict", "environment")
    return diagnostic.message


def test_a_foreign_hook_is_never_overwritten(repo: GitRepo) -> None:
    path = hook_path(repo)
    path.write_bytes(FOREIGN)
    path.chmod(0o750)
    before = path.stat()
    assert inspect(repo.repository(), HookName.PRE_COMMIT).status is HookStatus.FOREIGN
    message = assert_conflict(repo.repository())
    assert str(path) in message
    assert "pre-commit.local" in message
    after = path.stat()
    assert path.read_bytes() == FOREIGN
    assert (after.st_mode, after.st_mtime_ns, after.st_ino) == (
        before.st_mode,
        before.st_mtime_ns,
        before.st_ino,
    )


def test_a_managed_marker_of_another_hook_is_foreign(repo: GitRepo) -> None:
    hook_path(repo, HookName.PRE_PUSH).write_text(script(HookName.PRE_COMMIT))
    assert inspect(repo.repository(), HookName.PRE_PUSH).status is HookStatus.FOREIGN


def test_symlinks_and_directories_are_foreign(repo: GitRepo, tmp_path: Path) -> None:
    target = tmp_path / "shared-hook"
    target.write_text(script(HookName.PRE_COMMIT))
    hook_path(repo).symlink_to(target)
    hook_path(repo, HookName.PRE_PUSH).mkdir()
    for name in HookName:
        assert inspect(repo.repository(), name).status is HookStatus.FOREIGN
        assert_conflict(repo.repository(), name)
    assert hook_path(repo).is_symlink()
    assert target.read_text() == script(HookName.PRE_COMMIT)


def test_an_oversized_file_is_foreign(repo: GitRepo) -> None:
    hook_path(repo).write_bytes(script(HookName.PRE_COMMIT).encode() + b"#" * 1024 * 1024)
    assert inspect(repo.repository(), HookName.PRE_COMMIT).status is HookStatus.FOREIGN


def test_core_hooks_path_is_a_conflict(repo: GitRepo) -> None:
    repo.git("config", "core.hooksPath", ".githooks")
    with pytest.raises(HookConflictError) as raised:
        hooks_directory(repo.repository())
    assert "core.hooksPath is set to '.githooks'" in raised.value.diagnostic.message
    assert_conflict(repo.repository())
    assert not (repo.path / ".githooks").exists()
    assert not hook_path(repo).exists()


def test_an_unreadable_git_config_is_a_git_failure(repo: GitRepo) -> None:
    (repo.path / ".git" / "config").write_text("[core\n")
    with pytest.raises(GitioError) as raised:
        hooks_directory(Repository(repo.path, repo.path / ".git", repo.environ()))
    assert raised.value.diagnostic.rule == "git.failed"


def test_a_linked_worktree_uses_the_common_hooks_directory(repo: GitRepo, tmp_path: Path) -> None:
    repo.commit_all("base")
    linked = tmp_path / "linked"
    repo.git("worktree", "add", "-q", "-b", "topic", str(linked))
    worktree = Repository.discover(linked, repo.environ())
    assert hooks_directory(worktree).resolve() == hook_path(repo).parent.resolve()
    install(worktree, HookName.PRE_COMMIT)
    assert inspect(repo.repository(), HookName.PRE_COMMIT).status is HookStatus.CURRENT


def test_a_failed_write_leaves_no_temporary_file(
    repo: GitRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(source: Path, target: Path) -> Path:
        raise OSError(errno.ENOSPC, "disk full", str(target))

    monkeypatch.setattr(Path, "replace", refuse)
    with pytest.raises(OSError, match="disk full"):
        install(repo.repository(), HookName.PRE_COMMIT)
    directory = hook_path(repo).parent
    assert not hook_path(repo).exists()
    assert not [path for path in directory.iterdir() if path.name.startswith(".")]
