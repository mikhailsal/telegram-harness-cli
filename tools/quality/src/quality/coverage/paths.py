"""Map file names written by native tools to repository paths inside the snapshot."""

from __future__ import annotations

import posixpath
from pathlib import Path

from quality.boundedio import ReportError


def repository_path(raw: str, *, root: Path, cwd: Path) -> str:
    """Return the repository path of a reported file, which must lie inside `root`.

    Relative names are resolved against `cwd`, where the tool ran. The snapshot
    path and its resolved form are both accepted, because tools print either.
    """
    if not raw or "\0" in raw:
        msg = f"invalid file name in report: {raw!r}"
        raise ReportError(msg)
    joined = posixpath.normpath(posixpath.join(cwd.as_posix(), raw))
    for base in dict.fromkeys((root.as_posix(), root.resolve().as_posix())):
        if joined.startswith(base + "/"):
            return joined.removeprefix(base + "/")
    msg = f"report names a file outside the snapshot: {raw}"
    raise ReportError(msg)
