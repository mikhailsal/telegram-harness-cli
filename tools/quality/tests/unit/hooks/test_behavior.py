import shlex
from dataclasses import dataclass
from pathlib import Path

import pytest

from quality.hooks import HookName, install
from quality.process import Command, Completed, run
from quality.testkit.gitrepo import GitRepo

ZERO = "0" * 40


@dataclass(frozen=True, slots=True)
class Hooked:
    """A repository whose `./quality` is a stub that logs its arguments."""

    repo: GitRepo
    log: Path
    status: Path

    def calls(self) -> list[str]:
        return self.log.read_text().splitlines() if self.log.exists() else []

    def exits(self, code: int) -> None:
        self.status.write_text(f"{code}\n")

    def local_hook(self, name: HookName, code: int) -> None:
        hooks = self.repo.path / ".git" / "hooks"
        body = f"printf 'local %s\\n' \"$*\" >> {shlex.quote(str(self.log))}\n"
        if name is HookName.PRE_PUSH:
            body += f"cat > {shlex.quote(str(self.log))}.stdin\n"
        (hooks / f"{name}.local").write_text(f"#!/bin/sh\n{body}exit {code}\n")
        (hooks / f"{name}.local").chmod(0o755)


@pytest.fixture
def hooked(repo: GitRepo, tmp_path: Path) -> Hooked:
    repo.write("README.md", "# Project\n")
    repo.commit_all("base")
    log, status = tmp_path / "calls.log", tmp_path / "status"
    stub = (
        "#!/bin/sh\n"
        f"printf 'quality %s\\n' \"$*\" >> {shlex.quote(str(log))}\n"
        f'exit "$(cat {shlex.quote(str(status))})"\n'
    )
    repo.write("quality", stub, executable=True)
    hooked = Hooked(repo, log, status)
    hooked.exits(0)
    return hooked


def commit(hooked: Hooked, content: str) -> Completed:
    hooked.repo.write("app.txt", content)
    hooked.repo.git("add", "app.txt")
    return hooked.repo.run_git("commit", "-q", "-m", "change")


def test_a_commit_passes_only_through_the_staged_check(hooked: Hooked) -> None:
    base = hooked.repo.head()
    install(hooked.repo.repository(), HookName.PRE_COMMIT)
    assert commit(hooked, "good\n").ok
    assert hooked.calls() == ["quality check --staged"]
    assert hooked.repo.head() != base


def test_a_rejected_commit_leaves_the_index_and_work_tree_alone(hooked: Hooked) -> None:
    install(hooked.repo.repository(), HookName.PRE_COMMIT)
    hooked.exits(1)
    hooked.repo.write("app.txt", "staged\n")
    hooked.repo.git("add", "app.txt")
    hooked.repo.write("app.txt", "unstaged on top\n")
    before = hooked.repo.git("ls-files", "-s"), hooked.repo.git("status", "--porcelain")
    base = hooked.repo.head()
    result = hooked.repo.run_git("commit", "-q", "-m", "rejected")
    assert not result.ok
    assert (hooked.repo.git("ls-files", "-s"), hooked.repo.git("status", "--porcelain")) == before
    assert (hooked.repo.path / "app.txt").read_text() == "unstaged on top\n"
    assert hooked.repo.head() == base
    assert hooked.calls() == ["quality check --staged"]


def test_the_local_hook_runs_first_and_can_block(hooked: Hooked) -> None:
    install(hooked.repo.repository(), HookName.PRE_COMMIT)
    hooked.local_hook(HookName.PRE_COMMIT, 0)
    assert commit(hooked, "one\n").ok
    hooked.local_hook(HookName.PRE_COMMIT, 1)
    assert not commit(hooked, "two\n").ok
    assert hooked.calls() == ["local ", "quality check --staged", "local "]


def push_hook(hooked: Hooked, updates: str) -> Completed:
    path = install(hooked.repo.repository(), HookName.PRE_PUSH).path
    argv = ("sh", str(path), "origin", "file:///remote.git")
    command = Command(
        argv, cwd=hooked.repo.path, timeout=30, env=hooked.repo.environ(), stdin=updates.encode()
    )
    return run(command)


UPDATES = (
    f"refs/heads/main {'1' * 40} refs/heads/main {ZERO}\n"
    f"refs/heads/gone {ZERO} refs/heads/gone {'2' * 40}\n"
    f"refs/heads/next {'3' * 64} refs/heads/next {'4' * 64}\n"
    f"refs/heads/old {'0' * 64} refs/heads/old {'5' * 64}\n"
)


def test_the_pre_push_hook_checks_each_pushed_commit(hooked: Hooked) -> None:
    result = push_hook(hooked, UPDATES)
    assert result.returncode == 0, result.stderr_text()
    assert hooked.calls() == [
        f"quality check --rev {'1' * 40} --all",
        f"quality check --rev {'3' * 64} --all",
    ]


def test_the_pre_push_hook_stops_at_the_first_failure(hooked: Hooked) -> None:
    hooked.exits(3)
    result = push_hook(hooked, UPDATES)
    assert result.returncode == 3
    assert hooked.calls() == [f"quality check --rev {'1' * 40} --all"]


def test_the_local_pre_push_hook_sees_the_same_updates(hooked: Hooked) -> None:
    hooked.local_hook(HookName.PRE_PUSH, 0)
    assert push_hook(hooked, UPDATES).ok
    assert Path(f"{hooked.log}.stdin").read_text() == UPDATES
    assert hooked.calls()[0] == "local origin file:///remote.git"
    hooked.local_hook(HookName.PRE_PUSH, 1)
    assert not push_hook(hooked, UPDATES).ok
    assert hooked.calls()[-1] == "local origin file:///remote.git"


def test_a_real_push_runs_the_check_and_a_failure_stops_it(hooked: Hooked, tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    hooked.repo.git("init", "-q", "--bare", str(remote))
    hooked.repo.git("remote", "add", "origin", str(remote))
    install(hooked.repo.repository(), HookName.PRE_PUSH)
    hooked.exits(1)
    assert not hooked.repo.run_git("push", "-q", "origin", "main").ok
    assert hooked.repo.git("ls-remote", str(remote)) == ""
    hooked.exits(0)
    assert hooked.repo.run_git("push", "-q", "origin", "main").ok
    head = hooked.repo.head()
    assert hooked.calls() == [f"quality check --rev {head} --all"] * 2
    assert hooked.repo.git("ls-remote", str(remote), "refs/heads/main").split()[0] == head
