"""A Git repository addressed explicitly; ambient Git variables are never inherited."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from quality.gitio.errors import GIT_FAILED, GIT_REVISION_UNRESOLVABLE, GitioError
from quality.process import Command, Completed, ProcessError, run, sanitized

GIT_TIMEOUT = 600.0
OUTPUT_LIMIT = 512 * 1024 * 1024
# Configuration that could run code or change plumbing output is pinned per call.
PINNED_CONFIG = (
    "-c",
    "core.fsmonitor=false",
    "-c",
    "core.quotePath=false",
    "-c",
    "color.ui=false",
)
REPOSITORY_VARIABLES = ("GIT_DIR", "GIT_WORK_TREE")
# A commit the core creates carries the user's identity: from these variables, or
# from their Git configuration under HOME. None is invented, and Git may not guess one.
IDENTITY_VARIABLES = (
    "GIT_AUTHOR_NAME",
    "GIT_AUTHOR_EMAIL",
    "GIT_AUTHOR_DATE",
    "GIT_COMMITTER_NAME",
    "GIT_COMMITTER_EMAIL",
    "GIT_COMMITTER_DATE",
)
NO_GUESSED_IDENTITY = {
    "GIT_CONFIG_COUNT": "1",
    "GIT_CONFIG_KEY_0": "user.useConfigOnly",
    "GIT_CONFIG_VALUE_0": "true",
}


@dataclass(frozen=True, slots=True)
class Repository:
    """A work tree and Git directory with the environment used for every Git call."""

    worktree: Path
    git_dir: Path
    env: Mapping[str, str]

    @classmethod
    def discover(cls, cwd: Path, environ: Mapping[str, str]) -> Repository:
        """Locate the repository of `cwd`, honoring GIT_DIR and GIT_WORK_TREE deliberately."""
        explicit = {key: environ[key] for key in REPOSITORY_VARIABLES if key in environ}
        env = sanitized(environ, extra=explicit)
        probe = _git(cwd.resolve(), env, ("rev-parse", "--absolute-git-dir", "--show-toplevel"))
        match probe.stdout_text().splitlines():
            case [git_dir, worktree] if probe.ok:
                fixed = sanitized(environ, extra={"GIT_DIR": git_dir, "GIT_WORK_TREE": worktree})
                return cls(worktree=Path(worktree), git_dir=Path(git_dir), env=fixed)
            case _:
                detail = probe.stderr_text().strip() or "not a Git work tree"
                raise _failure(command="rev-parse", detail=detail)

    def git(
        self,
        *args: str,
        stdin: bytes | None = None,
        env: Mapping[str, str] | None = None,
        output_limit: int = OUTPUT_LIMIT,
    ) -> Completed:
        """Run a Git command and fail on a nonzero exit."""
        result = self.try_git(*args, stdin=stdin, env=env, output_limit=output_limit)
        if not result.ok:
            raise _failure(args[0], result.stderr_text().strip() or f"exit {result.returncode}")
        return result

    def try_git(
        self,
        *args: str,
        stdin: bytes | None = None,
        env: Mapping[str, str] | None = None,
        output_limit: int = OUTPUT_LIMIT,
    ) -> Completed:
        """Run a Git command and return its result whatever the exit status."""
        return _git(self.worktree, env or self.env, args, stdin=stdin, output_limit=output_limit)

    def default_index(self) -> Path:
        """Return the index file Git uses when GIT_INDEX_FILE is not set."""
        raw = self.git("rev-parse", "--git-path", "index").stdout_text().strip()
        return self.worktree / raw

    def resolve_commit(self, revision: str) -> str:
        """Resolve a revision to a full commit ID or fail explicitly."""
        if not revision or revision.startswith("-"):
            raise _unresolvable(revision, "not a revision name")
        result = self.try_git(
            "rev-parse", "--verify", "--quiet", "--end-of-options", f"{revision}^{{commit}}"
        )
        if not result.ok:
            raise _unresolvable(revision, "no such commit in this clone")
        return result.stdout_text().strip()

    def head_commit(self) -> str | None:
        """Return the commit HEAD points at, or None before the first commit."""
        result = self.try_git("rev-parse", "--verify", "--quiet", "HEAD^{commit}")
        return result.stdout_text().strip() if result.ok else None

    def tree_of(self, commit: str) -> str:
        """Return the tree ID of a commit ID."""
        return self.git("rev-parse", "--verify", f"{commit}^{{tree}}").stdout_text().strip()

    def empty_tree(self) -> str:
        """Return the empty tree ID in this repository's hash algorithm."""
        return self.git("hash-object", "-t", "tree", "--stdin", stdin=b"").stdout_text().strip()

    def merge_base(self, left: str, right: str) -> str:
        """Return the best common ancestor of two commits or fail explicitly."""
        result = self.try_git("merge-base", left, right)
        if not result.ok:
            pair = f"{left} {right}"
            raise _unresolvable(pair, "no merge base in this clone")
        return result.stdout_text().strip()

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        """Whether `ancestor` is reachable from `descendant`."""
        return self.try_git("merge-base", "--is-ancestor", ancestor, descendant).ok

    def shallow_commits(self) -> frozenset[str]:
        """Return the commits whose parents a shallow clone lacks; none for a full clone."""
        raw = self.git("rev-parse", "--git-path", "shallow").stdout_text().strip()
        try:
            listing = (self.worktree / raw).read_text(encoding="ascii")
        except FileNotFoundError:
            return frozenset()
        return frozenset(listing.split())


def create_repository(
    path: Path, *, branch: str, message: str, environ: Mapping[str, str]
) -> Repository:
    """Make the directory `path` a repository whose first commit holds all of its files.

    Ignored files are committed too, so the commit holds exactly what the
    directory holds. Ambient Git variables are not inherited, apart from the
    user's identity.
    """
    identity = {key: environ[key] for key in IDENTITY_VARIABLES if key in environ}
    init = _git(
        path, sanitized(environ, extra=identity), ("init", "-q", f"--initial-branch={branch}")
    )
    if not init.ok:
        detail = init.stderr_text().strip() or f"exit {init.returncode}"
        raise _failure(command="init", detail=detail)
    place = {"GIT_DIR": str(path / ".git"), "GIT_WORK_TREE": str(path)}
    repo = Repository(path, path / ".git", sanitized(environ, extra={**identity, **place}))
    repo.git("add", "--all", "--force")
    strict = {**repo.env, **NO_GUESSED_IDENTITY}
    repo.git("commit", "--quiet", "--message", message, env=strict)
    return repo


def _git(
    cwd: Path,
    env: Mapping[str, str],
    args: tuple[str, ...],
    *,
    stdin: bytes | None = None,
    output_limit: int = OUTPUT_LIMIT,
) -> Completed:
    argv = ("git", *PINNED_CONFIG, *args)
    command = Command(
        argv, cwd=cwd, timeout=GIT_TIMEOUT, env=env, stdin=stdin, output_limit=output_limit
    )
    try:
        result = run(command)
    except ProcessError as exc:
        raise _failure(args[0], str(exc)) from exc
    if result.stdout_truncated:
        raise _failure(args[0], f"output exceeded {output_limit} bytes")
    return result


def _failure(command: str, detail: str) -> GitioError:
    return GitioError(GIT_FAILED.diagnostic(f"git {command} failed: {detail}"))


def _unresolvable(revision: str, detail: str) -> GitioError:
    return GitioError(GIT_REVISION_UNRESOLVABLE.diagnostic(f"'{revision}': {detail}"))
