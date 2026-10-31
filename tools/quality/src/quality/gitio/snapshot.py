"""Materialize a candidate tree in an isolated temporary directory.

Blob bytes come from `git cat-file --batch`, so no clean, smudge, or line-ending
filter runs and nothing from the work tree or the index file is read.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from quality.diagnostics import Location
from quality.gitio.errors import GIT_SYMLINK_ESCAPE, SNAPSHOT_NOT_ISOLATED, GitioError
from quality.gitio.repo import Repository
from quality.gitio.tree import TreeEntry, list_tree, read_blobs

# A snapshot is `<temporary directory>/quality-snapshot-*/candidate`, with `work` beside it.
SNAPSHOT_PREFIX = "quality-snapshot-"
CANDIDATE_DIRECTORY = "candidate"
# Files that tools discover in parent directories and that must not exist above a snapshot.
ISOLATION_MARKERS = (
    ".git",
    "quality.toml",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "pytest.ini",
    "tox.ini",
    "conftest.py",
    "package.json",
    "node_modules",
    "tsconfig.json",
    "pubspec.yaml",
    ".dart_tool",
    "composer.json",
)


@dataclass(frozen=True, slots=True)
class Snapshot:
    """A materialized tree (`root`) with private scratch space (`work`) beside it."""

    tree: str
    root: Path
    work: Path
    entries: tuple[TreeEntry, ...]

    @property
    def gitlinks(self) -> tuple[TreeEntry, ...]:
        """Submodule entries; they are materialized as empty directories."""
        return tuple(entry for entry in self.entries if entry.is_gitlink)


def check_isolated(directory: Path) -> None:
    """Fail if a parent of `directory` contains a project marker tools would discover."""
    for parent in (directory, *directory.parents):
        for marker in ISOLATION_MARKERS:
            if os.path.lexists(parent / marker):
                message = f"'{parent / marker}' is visible from snapshots in '{directory}'"
                raise GitioError(SNAPSHOT_NOT_ISOLATED.diagnostic(message))


def _write_file(path: Path, data: bytes, *, executable: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)
        os.fchmod(stream.fileno(), 0o755 if executable else 0o644)


def _escape(entry: TreeEntry, target: str) -> GitioError:
    message = f"symbolic link target '{target}' leaves the candidate tree"
    return GitioError(GIT_SYMLINK_ESCAPE.diagnostic(message, location=Location(entry.path)))


def _link(root: Path, entry: TreeEntry, target: str) -> None:
    if Path(target).is_absolute():
        raise _escape(entry, target)
    link = root / entry.path
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target)


def _check_links(root: Path, links: dict[TreeEntry, str]) -> None:
    real_root = Path(os.path.realpath(root))
    for entry, target in links.items():
        if not Path(os.path.realpath(root / entry.path)).is_relative_to(real_root):
            raise _escape(entry, target)


def _populate(repo: Repository, root: Path, entries: tuple[TreeEntry, ...]) -> None:
    by_oid: dict[str, list[TreeEntry]] = {}
    for entry in entries:
        if entry.is_file or entry.is_symlink:
            by_oid.setdefault(entry.oid, []).append(entry)
        else:  # A recursive listing holds only blobs and gitlinks.
            (root / entry.path).mkdir(parents=True, exist_ok=True)
    links: dict[TreeEntry, str] = {}
    blobs = ((oid, users[0].size) for oid, users in by_oid.items())
    for oid, data in read_blobs(repo, blobs):
        for entry in by_oid[oid]:
            if entry.is_symlink:
                links[entry] = os.fsdecode(data)
            else:
                _write_file(root / entry.path, data, executable=entry.is_executable)
    for entry, target in links.items():
        _link(root, entry, target)
    _check_links(root, links)


@contextmanager
def materialize(repo: Repository, tree: str, *, base: Path | None = None) -> Generator[Snapshot]:
    """Write `tree` to a fresh private directory and remove it afterwards."""
    parent = (base or Path(tempfile.gettempdir())).resolve()
    check_isolated(parent)
    top = Path(tempfile.mkdtemp(prefix=SNAPSHOT_PREFIX, dir=parent))
    try:
        root, work = top / CANDIDATE_DIRECTORY, top / "work"
        root.mkdir()
        work.mkdir()
        entries = list_tree(repo, tree)
        _populate(repo, root, entries)
        yield Snapshot(tree=tree, root=root, work=work, entries=entries)
    finally:
        shutil.rmtree(top, ignore_errors=True)
