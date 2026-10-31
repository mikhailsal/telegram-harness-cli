"""Which lines of a tested tree are new for commit, push, pull-request, and queue checks.

See decision 0004. Lines are reported per path of the tested tree.
"""

from __future__ import annotations

import re
from dataclasses import replace

from quality.gitio.candidate import Candidate, revision
from quality.gitio.diff import LineSet, added_lines, carry_lines
from quality.gitio.errors import GIT_REVISION_UNRESOLVABLE, GitioError
from quality.gitio.history import RevisionRange
from quality.gitio.repo import Repository

ZERO_REVISION = re.compile(r"^0+$")


def commit_lines(repo: Repository, candidate: Candidate) -> LineSet:
    """Lines added by a commit candidate relative to its base tree (HEAD or empty)."""
    return added_lines(repo, candidate.base_tree, candidate.tree)


def changed_lines(repo: Repository, candidate: Candidate) -> LineSet:
    """Return the candidate's own new lines, or else those added since its base tree."""
    return candidate.lines if candidate.lines is not None else commit_lines(repo, candidate)


def push_lines(repo: Repository, before: str, after: str) -> LineSet:
    """Lines added by a push; an all-zero `before` means a new branch with no base."""
    old_tree = (
        repo.empty_tree()
        if ZERO_REVISION.match(before)
        else repo.tree_of(repo.resolve_commit(before))
    )
    return added_lines(repo, old_tree, repo.tree_of(repo.resolve_commit(after)))


def merge_queue_lines(repo: Repository, base: str, tested: str) -> LineSet:
    """Lines a merge-queue entry adds on top of the base branch."""
    return added_lines(
        repo,
        repo.tree_of(repo.resolve_commit(base)),
        repo.tree_of(repo.resolve_commit(tested)),
    )


def pull_request_lines(repo: Repository, base: str, head: str, tested: str) -> LineSet:
    """Lines the pull request contributes to the tested tree.

    Lines added between the merge base and the head are carried into the tested
    tree. Lines that differ from both the base and the head in the tested tree
    (conflict resolutions) are added. Lines that came unchanged from the base
    branch are never included.
    """
    base_commit = repo.resolve_commit(base)
    head_commit = repo.resolve_commit(head)
    tested_commit = repo.resolve_commit(tested)
    if not repo.is_ancestor(head_commit, tested_commit):
        message = f"tested revision '{tested}' does not contain head '{head}'"
        raise GitioError(GIT_REVISION_UNRESOLVABLE.diagnostic(message))
    merge_base = repo.merge_base(base_commit, head_commit)
    base_tree, head_tree = repo.tree_of(base_commit), repo.tree_of(head_commit)
    tested_tree = repo.tree_of(tested_commit)
    contributed = carry_lines(
        repo, added_lines(repo, repo.tree_of(merge_base), head_tree), head_tree, tested_tree
    )
    from_base = added_lines(repo, base_tree, tested_tree)
    from_head = added_lines(repo, head_tree, tested_tree)
    result = dict(contributed)
    for path, numbers in from_base.items():
        resolved = numbers & from_head.get(path, frozenset())
        if resolved:
            result[path] = result.get(path, frozenset()) | resolved
    return result


def push_candidate(repo: Repository, before: str, after: str) -> Candidate:
    """Return a pushed tip compared with the previous tip, with its new commits.

    An all-zero `before` is a new branch: the base is the empty tree and the
    whole history of the tip is new.
    """
    tip = repo.resolve_commit(after)
    base = None if ZERO_REVISION.match(before) else repo.resolve_commit(before)
    return replace(
        revision(repo, after),
        base_tree=repo.empty_tree() if base is None else repo.tree_of(base),
        lines=push_lines(repo, before, tip),
        history=RevisionRange(base, tip),
    )


def merge_candidate(
    repo: Repository, base: str, head: str, tested: str | None = None, *, queue: bool = False
) -> Candidate:
    """Return the tested tree of a pull request or a merge-queue entry (decision 0004).

    For a pull request, the new lines are those of `pull_request_lines`; for a
    merge-queue entry, which is built on the base, those the tested tree adds
    to the base. The new history is every commit of the tested tree that the
    base lacks.
    """
    name = tested or head
    base_commit = repo.resolve_commit(base)
    tested_commit = repo.resolve_commit(name)
    if queue:
        if not repo.is_ancestor(base_commit, tested_commit):
            message = f"merge-queue revision '{name}' is not built on base '{base}'"
            raise GitioError(GIT_REVISION_UNRESOLVABLE.diagnostic(message))
        lines = merge_queue_lines(repo, base_commit, tested_commit)
    else:
        lines = pull_request_lines(repo, base_commit, repo.resolve_commit(head), tested_commit)
    return replace(
        revision(repo, name),
        base_tree=repo.tree_of(base_commit),
        lines=lines,
        history=RevisionRange(base_commit, tested_commit),
    )
