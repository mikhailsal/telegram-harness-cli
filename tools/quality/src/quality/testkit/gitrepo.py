"""Temporary Git repositories for tests: fixed robot identity, no user or system config."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from quality.gitio import Repository
from quality.process import Command, Completed, run, sanitized

IDENTITY = {
    "GIT_AUTHOR_NAME": "Quality Test",
    "GIT_AUTHOR_EMAIL": "quality-test@example.invalid",
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z",
    "GIT_COMMITTER_NAME": "Quality Test",
    "GIT_COMMITTER_EMAIL": "quality-test@example.invalid",
    "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
}


class GitCommandError(AssertionError):
    """A Git command used to arrange a test failed."""


@dataclass(frozen=True, slots=True)
class GitRepo:
    """A throwaway repository; `home` is an empty HOME so no user config applies."""

    path: Path
    home: Path

    @classmethod
    def create(cls, path: Path, *, branch: str = "main") -> GitRepo:
        """Initialize a new repository at `path` with an isolated HOME beside it."""
        home = path.parent / f"{path.name}-home"
        home.mkdir(parents=True, exist_ok=True)
        path.mkdir(parents=True, exist_ok=True)
        repo = cls(path=path, home=home)
        repo.git("init", "-q", "-b", branch)
        return repo

    def environ(self, **extra: str) -> dict[str, str]:
        """Return the environment of a user working in this repository."""
        env = sanitized({"HOME": str(self.home)}, extra={**IDENTITY, "GIT_CONFIG_NOSYSTEM": "1"})
        return {**env, **extra}

    def run_git(self, *args: str, stdin: bytes | None = None, **extra: str) -> Completed:
        """Run Git as the user would and return the result."""
        command = Command(
            ("git", *args), cwd=self.path, timeout=60, env=self.environ(**extra), stdin=stdin
        )
        return run(command)

    def git(self, *args: str, stdin: bytes | None = None, **extra: str) -> str:
        """Run Git as the user would and return stdout; fail the test on error."""
        result = self.run_git(*args, stdin=stdin, **extra)
        if not result.ok:
            message = f"git {' '.join(args)} failed: {result.stderr_text()}"
            raise GitCommandError(message)
        return result.stdout_text()

    def write(self, name: str | bytes, content: str | bytes, *, executable: bool = False) -> Path:
        """Write a work-tree file; `name` may be bytes for non-UTF-8 paths."""
        target = self.path / os.fsdecode(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content.encode() if isinstance(content, str) else content)
        target.chmod(0o755 if executable else 0o644)
        return target

    def commit_all(self, message: str) -> str:
        """Stage everything and commit; return the commit ID."""
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", message)
        return self.head()

    def head(self) -> str:
        """Return the current HEAD commit ID."""
        return self.git("rev-parse", "HEAD").strip()

    def repository(self) -> Repository:
        """Return the production Repository view of this repository."""
        return Repository.discover(self.path, self.environ())
