"""Blobs introduced by a revision range, for history-wide secret and size checks."""

from __future__ import annotations

import os
from dataclasses import dataclass

from quality.gitio.errors import GIT_FAILED, GIT_REVISION_UNRESOLVABLE, GitioError
from quality.gitio.repo import Repository

HISTORY_OUTPUT_LIMIT = 512 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class HistoryBlob:
    """A blob reachable from the tip but not from the base, with a path it was added at."""

    oid: str
    size: int
    path: str


@dataclass(frozen=True, slots=True)
class RevisionRange:
    """Commits reachable from `tip` but not from `base`; no base means the whole history."""

    base: str | None
    tip: str

    def arguments(self) -> tuple[str, ...]:
        """Revision arguments for `git rev-list` and `git log`."""
        return (self.tip,) if self.base is None else (self.tip, f"^{self.base}")


def _names(repo: Repository, span: RevisionRange) -> dict[str, str]:
    """Map blob IDs to the first path at which a new commit added or changed them."""
    output = repo.git(
        "log",
        "--raw",
        "-z",
        "--no-abbrev",
        "--no-renames",
        "-m",
        "--root",
        "--reverse",
        "--format=",
        "--end-of-options",
        *span.arguments(),
        output_limit=HISTORY_OUTPUT_LIMIT,
    ).stdout
    names: dict[str, str] = {}
    fields = iter(output.split(b"\0"))
    for field in fields:
        if field.startswith(b":"):
            oid = field.decode("ascii").split()[3]
            names.setdefault(oid, os.fsdecode(next(fields, b"")))
    return names


def check_complete(repo: Repository, span: RevisionRange) -> None:
    """Fail when a shallow clone cut the range, so that older new commits are missing."""
    shallow = repo.shallow_commits()
    if not shallow:
        return
    commits = repo.git(
        "rev-list", "--end-of-options", *span.arguments(), output_limit=HISTORY_OUTPUT_LIMIT
    ).stdout_text()
    cut = sorted(shallow.intersection(commits.split()))
    if cut:
        message = f"the history of '{span.tip}' is cut at shallow commit {cut[0]}"
        raise GitioError(GIT_REVISION_UNRESOLVABLE.diagnostic(message))


def new_blobs(repo: Repository, span: RevisionRange) -> tuple[HistoryBlob, ...]:
    """Every blob of the range, including blobs that were deleted again before the tip.

    A range that a shallow clone cut fails rather than checking part of it.
    """
    check_complete(repo, span)
    objects = repo.git(
        "rev-list",
        "--objects",
        "--no-object-names",
        "--end-of-options",
        *span.arguments(),
        output_limit=HISTORY_OUTPUT_LIMIT,
    ).stdout
    oids = objects.split()
    if not oids:
        return ()
    checked = repo.git(
        "cat-file",
        "--batch-check=%(objectname) %(objecttype) %(objectsize)",
        stdin=b"".join(oid + b"\n" for oid in oids),
        output_limit=HISTORY_OUTPUT_LIMIT,
    ).stdout_text()
    names = _names(repo, span)
    blobs: list[HistoryBlob] = []
    for line in checked.splitlines():
        match line.split():
            case [oid, "blob", size]:
                blobs.append(HistoryBlob(oid, int(size), names.get(oid, oid)))
            case [_, "commit" | "tree" | "tag", _]:
                pass
            case _:
                raise GitioError(GIT_FAILED.diagnostic(f"git cat-file: unexpected '{line}'"))
    return tuple(sorted(blobs, key=lambda blob: (blob.path, blob.oid)))
