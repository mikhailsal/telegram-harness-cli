"""Tree listings and raw blob reads through Git plumbing."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from quality.gitio.errors import GIT_FAILED, GitioError
from quality.gitio.repo import Repository

REGULAR = "100644"
EXECUTABLE = "100755"
SYMLINK = "120000"
GITLINK = "160000"
CHUNK_BYTES = 32 * 1024 * 1024
HEADER_SLACK = 160


@dataclass(frozen=True, slots=True)
class TreeEntry:
    """One path of a tree; paths are decoded with surrogate escapes."""

    mode: str
    oid: str
    size: int
    path: str

    @property
    def is_file(self) -> bool:
        """A regular or executable file."""
        return self.mode in {REGULAR, EXECUTABLE}

    @property
    def is_executable(self) -> bool:
        """A file with the executable bit."""
        return self.mode == EXECUTABLE

    @property
    def is_symlink(self) -> bool:
        """A symbolic link; its blob holds the target."""
        return self.mode == SYMLINK

    @property
    def is_gitlink(self) -> bool:
        """A submodule commit reference."""
        return self.mode == GITLINK


def blob_id(data: bytes, *, algorithm: str = "sha1") -> str:
    """Return the object ID Git gives a blob with these bytes ("sha1" or "sha256")."""
    digest = hashlib.new(algorithm)
    digest.update(b"blob %d\0" % len(data))
    digest.update(data)
    return digest.hexdigest()


def list_tree(repo: Repository, tree: str) -> tuple[TreeEntry, ...]:
    """Every path of a tree, recursively, in Git order."""
    output = repo.git("ls-tree", "-r", "-z", "-l", "--full-tree", tree).stdout
    entries: list[TreeEntry] = []
    for record in output.split(b"\0"):
        if not record:
            continue
        meta, _, raw_path = record.partition(b"\t")
        mode, _kind, oid, size = meta.decode("ascii").split()
        entries.append(
            TreeEntry(
                mode=mode,
                oid=oid,
                size=0 if size == "-" else int(size),
                path=os.fsdecode(raw_path),
            )
        )
    return tuple(entries)


def _chunks(blobs: list[tuple[str, int]]) -> Iterator[list[tuple[str, int]]]:
    chunk: list[tuple[str, int]] = []
    total = 0
    for oid, size in blobs:
        if chunk and total + size > CHUNK_BYTES:
            yield chunk
            chunk, total = [], 0
        chunk.append((oid, size))
        total += size
    if chunk:
        yield chunk


def _parse_batch(output: bytes) -> Iterator[tuple[str, bytes]]:
    position = 0
    while position < len(output):
        end = output.index(b"\n", position)
        header = output[position:end].decode("ascii", errors="replace")
        match header.split():
            case [oid, "blob", size_text] if size_text.isdigit():
                size = int(size_text)
            case _:
                message = f"git cat-file: unexpected object header '{header}'"
                raise GitioError(GIT_FAILED.diagnostic(message))
        start = end + 1
        yield oid, output[start : start + size]
        position = start + size + 1


def blob_sizes(repo: Repository, oids: Iterable[str]) -> dict[str, int]:
    """Return the size of each blob, failing on missing or non-blob objects."""
    unique = list(dict.fromkeys(oids))
    if not unique:
        return {}
    stdin = b"".join(oid.encode("ascii") + b"\n" for oid in unique)
    sizes: dict[str, int] = {}
    for line in repo.git("cat-file", "--batch-check", stdin=stdin).stdout_text().splitlines():
        match line.split():
            case [oid, "blob", size] if size.isdigit():
                sizes[oid] = int(size)
            case _:
                raise GitioError(GIT_FAILED.diagnostic(f"git cat-file: unexpected '{line}'"))
    return sizes


def read_blobs(repo: Repository, blobs: Iterable[tuple[str, int]]) -> Iterator[tuple[str, bytes]]:
    """Yield the exact bytes of each distinct blob, without filters, in bounded chunks."""
    unique = list(dict(blobs).items())
    for chunk in _chunks(unique):
        stdin = b"".join(oid.encode("ascii") + b"\n" for oid, _ in chunk)
        limit = sum(size + HEADER_SLACK for _, size in chunk)
        yield from _parse_batch(
            repo.git("cat-file", "--batch", stdin=stdin, output_limit=limit).stdout
        )


def read_path(repo: Repository, tree: str, path: str, limit: int) -> bytes | None:
    """Return a regular file of a tree, or None when it is absent, not a file, or over `limit`."""
    found = [entry for entry in list_tree(repo, tree) if entry.path == path and entry.is_file]
    if not found or found[0].size > limit:
        return None
    return b"".join(data for _, data in read_blobs(repo, [(found[0].oid, found[0].size)]))
