"""Tree changes, unified-diff hunks, and line mapping between revisions."""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from quality.gitio.repo import Repository
from quality.gitio.tree import EXECUTABLE, REGULAR, blob_sizes, read_blobs

HUNK_HEADER = re.compile(rb"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", re.MULTILINE)
BINARY_PROBE = 8000
DIFF_OPTIONS = (
    "--no-ext-diff",
    "--no-textconv",
    "--no-color",
    "--unified=0",
    "--diff-algorithm=myers",
)

type LineSet = Mapping[str, frozenset[int]]


@dataclass(frozen=True, slots=True)
class FileChange:
    """One entry of `git diff-tree --raw`; `path` is the new path (old for deletions)."""

    status: str
    path: str
    old_path: str | None
    old_mode: str
    new_mode: str
    old_oid: str
    new_oid: str

    @property
    def new_is_file(self) -> bool:
        """The new side is a regular or executable file."""
        return self.new_mode in {REGULAR, EXECUTABLE}

    @property
    def old_is_file(self) -> bool:
        """The old side is a regular or executable file."""
        return self.old_mode in {REGULAR, EXECUTABLE}


@dataclass(frozen=True, slots=True)
class Hunk:
    """A changed region; a zero count means a pure insertion or deletion point."""

    old_start: int
    old_count: int
    new_start: int
    new_count: int

    def added(self) -> range:
        """New-side line numbers written by this hunk."""
        return range(self.new_start, self.new_start + self.new_count)


def tree_changes(repo: Repository, old_tree: str, new_tree: str) -> tuple[FileChange, ...]:
    """Return the changed paths between two trees, with rename detection."""
    output = repo.git("diff-tree", "-r", "-z", "-M", "--no-abbrev", old_tree, new_tree).stdout
    tokens = output.split(b"\0")
    changes: list[FileChange] = []
    index = 0
    while index < len(tokens) and tokens[index].startswith(b":"):
        old_mode, new_mode, old_oid, new_oid, status = tokens[index][1:].decode().split()
        paired = status[0] in {"R", "C"}
        first = os.fsdecode(tokens[index + 1])
        new_path = os.fsdecode(tokens[index + 2]) if paired else first
        changes.append(
            FileChange(
                status=status[0],
                path=new_path,
                old_path=first if paired else None,
                old_mode=old_mode,
                new_mode=new_mode,
                old_oid=old_oid,
                new_oid=new_oid,
            )
        )
        index += 3 if paired else 2
    return tuple(changes)


def parse_hunks(patch: bytes) -> tuple[Hunk, ...]:
    """Hunk headers of a unified diff with zero context."""
    return tuple(
        Hunk(
            old_start=int(match[1]),
            old_count=1 if match[2] is None else int(match[2]),
            new_start=int(match[3]),
            new_count=1 if match[4] is None else int(match[4]),
        )
        for match in HUNK_HEADER.finditer(patch)
    )


def blob_hunks(repo: Repository, old_oid: str, new_oid: str) -> tuple[Hunk, ...] | None:
    """Hunks between two blobs, or None when Git treats the content as binary."""
    if old_oid == new_oid:
        return ()
    patch = repo.git("diff", *DIFF_OPTIONS, old_oid, new_oid).stdout
    if re.search(rb"^Binary files ", patch, re.MULTILINE):
        return None
    return parse_hunks(patch)


def map_line(hunks: Iterable[Hunk], line: int) -> int | None:
    """Map an old-side line to the new side; None when the line was changed or removed."""
    delta = 0
    for hunk in hunks:
        if hunk.old_count and hunk.old_start <= line < hunk.old_start + hunk.old_count:
            return None
        before = (
            hunk.old_start < line
            if hunk.old_count == 0
            else hunk.old_start + hunk.old_count <= line
        )
        if not before:
            break
        delta += hunk.new_count - hunk.old_count
    return line + delta


def is_binary(data: bytes) -> bool:
    """Apply Git's heuristic: a NUL byte in the first 8000 bytes means binary."""
    return b"\0" in data[:BINARY_PROBE]


def text_lines(data: bytes) -> frozenset[int]:
    """Return every line number of a text blob; binary blobs have none."""
    if is_binary(data):
        return frozenset()
    count = data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)
    return frozenset(range(1, count + 1))


def _whole_files(repo: Repository, changes: list[FileChange]) -> dict[str, frozenset[int]]:
    paths: dict[str, list[str]] = {}
    for change in changes:
        paths.setdefault(change.new_oid, []).append(change.path)
    sizes = blob_sizes(repo, paths)
    result: dict[str, frozenset[int]] = {}
    for oid, data in read_blobs(repo, sizes.items()):
        for path in paths[oid]:
            result[path] = text_lines(data)
    return result


def _edited_lines(repo: Repository, change: FileChange) -> frozenset[int]:
    hunks = blob_hunks(repo, change.old_oid, change.new_oid)
    return frozenset(line for hunk in hunks or () for line in hunk.added())


def added_lines(repo: Repository, old_tree: str, new_tree: str) -> dict[str, frozenset[int]]:
    """Return the new-side lines added or modified in text files between two trees."""
    result: dict[str, frozenset[int]] = {}
    whole: list[FileChange] = []
    for change in tree_changes(repo, old_tree, new_tree):
        if change.status == "D" or not change.new_is_file:
            continue
        if change.status == "A" or not change.old_is_file:
            whole.append(change)
        else:
            result[change.path] = _edited_lines(repo, change)
    result.update(_whole_files(repo, whole))
    return {path: lines for path, lines in sorted(result.items()) if lines}


def carry_lines(repo: Repository, lines: LineSet, old_tree: str, new_tree: str) -> LineSet:
    """Follow line sets from one tree into another through renames and edits."""
    moves = {
        change.old_path or change.path: change for change in tree_changes(repo, old_tree, new_tree)
    }
    carried: dict[str, frozenset[int]] = {}
    for path, numbers in lines.items():
        change = moves.get(path)
        if change is None:
            carried[path] = numbers
            continue
        mapped = _moved_lines(repo, change, numbers)
        if mapped:
            carried[change.path] = carried.get(change.path, frozenset()) | mapped
    return carried


def _moved_lines(repo: Repository, change: FileChange, numbers: frozenset[int]) -> frozenset[int]:
    if change.status == "D" or not change.new_is_file or not change.old_is_file:
        return frozenset()
    hunks = blob_hunks(repo, change.old_oid, change.new_oid)
    if hunks is None:
        return frozenset()
    return frozenset(m for m in (map_line(hunks, n) for n in numbers) if m is not None)
