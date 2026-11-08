"""The temporary working-tree copy a fixer runs its tools in (decisions 0011 and 0012).

Fixers resolve the component's packages, which writes into the tree, so they
work on a copy of the working tree's tracked and untracked, not ignored,
regular files. A fixed file is copied back only while its working-tree content
is still the copied one, so an edit made in the meantime is never overwritten.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterable, Mapping
from pathlib import Path, PurePosixPath


def copy_tree(root: Path, target: Path, paths: Iterable[str], *, skipped: str) -> None:
    """Copy the regular files among `paths` from `root` to `target`, except below `skipped`."""
    for path in paths:
        source = root / path
        if skipped in PurePosixPath(path).parts or source.is_symlink() or not source.is_file():
            continue
        copy = target / path
        copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, copy)


def copy_back(copy: Path, root: Path, before: Mapping[str, bytes]) -> None:
    """Write each fixed file to the working tree unless it changed there meanwhile."""
    for path, original in before.items():
        fixed = (copy / path).read_bytes()
        current = root / path
        if fixed != original and current.read_bytes() == original:
            current.write_bytes(fixed)
