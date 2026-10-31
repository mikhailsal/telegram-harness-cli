"""Candidate identity: the exact tree a gate run validates."""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from quality.diagnostics import Diagnostic, Location
from quality.gitio.diff import LineSet
from quality.gitio.errors import GIT_CANDIDATE_CHANGED, GIT_UNMERGED, GitioError
from quality.gitio.history import RevisionRange
from quality.gitio.repo import Repository


@dataclass(frozen=True, slots=True)
class Candidate:
    """A tree to validate, the tree it is compared with, and how to recompute it.

    `lines` are the new lines when they are not simply those added since the
    base tree, as for a pull request. `history` is the range of new commits
    whose every blob is checked, for gates that receive history.
    """

    tree: str
    base_tree: str
    description: str
    resolve: Callable[[], str]
    lines: LineSet | None = None
    history: RevisionRange | None = None

    def verify_unchanged(self) -> None:
        """Recompute the candidate and fail if it no longer matches the validated tree."""
        current = self.resolve()
        if current != self.tree:
            message = f"{self.description} changed from tree {self.tree} to {current}"
            raise GitioError(GIT_CANDIDATE_CHANGED.diagnostic(message))


def index_tree(repo: Repository, index: Path) -> str:
    """Write the tree of an index file without modifying that file."""
    with tempfile.TemporaryDirectory(prefix="quality-index-") as scratch:
        copy = Path(scratch) / "index"
        if index.exists():
            shutil.copyfile(index, copy)
        env = {**repo.env, "GIT_INDEX_FILE": str(copy)}
        unmerged = repo.git("ls-files", "--unmerged", "-z", env=env).stdout
        if unmerged:
            raise GitioError(_unmerged(unmerged))
        return repo.git("write-tree", env=env).stdout_text().strip()


def _unmerged(listing: bytes) -> Diagnostic:
    paths = sorted({record.split(b"\t", 1)[1] for record in listing.split(b"\0") if record})
    more = f" (and {len(paths) - 1} more paths)" if len(paths) > 1 else ""
    location = Location(os.fsdecode(paths[0]))
    return GIT_UNMERGED.diagnostic(f"unresolved merge conflict{more}", location=location)


def staged(repo: Repository, index: Path | None = None) -> Candidate:
    """Return the commit candidate: the index (or alternate index) compared with HEAD."""
    source = index or repo.default_index()
    head = repo.head_commit()

    def resolve() -> str:
        return index_tree(repo, source)

    return Candidate(
        tree=resolve(),
        base_tree=repo.tree_of(head) if head else repo.empty_tree(),
        description=f"index {source}",
        resolve=resolve,
    )


def revision(repo: Repository, name: str) -> Candidate:
    """Return the candidate of a revision, compared with its first parent."""
    commit = repo.resolve_commit(name)
    parent = repo.try_git("rev-parse", "--verify", "--quiet", f"{commit}^1")

    def resolve() -> str:
        return repo.tree_of(repo.resolve_commit(name))

    return Candidate(
        tree=repo.tree_of(commit),
        base_tree=repo.tree_of(parent.stdout_text().strip()) if parent.ok else repo.empty_tree(),
        description=f"revision {name}",
        resolve=resolve,
    )
