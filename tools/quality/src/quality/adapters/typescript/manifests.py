"""Manifest agreement with npm's root and workspace lockfile entries, before install."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path, PurePosixPath

from quality.adapters.settings import Table, inside, load_json_object, table
from quality.adapters.typescript.lockfile import MANIFEST, Lock, workspace_patterns
from quality.boundedio import ReportError
from quality.diagnostics import Rule

LOCKFILE = Rule(
    id="typescript.lockfile",
    title="Package manifests disagree with the npm lockfile",
    requirement="R04",
    explanation=(
        "Before npm installs anything, the root and workspace manifests must agree with "
        "their package-lock.json entries. A changed, added, or removed dependency is a "
        "candidate finding, rather than a missing package in the machine's cache."
    ),
    remedy="Run `npm install` in the lockfile directory and stage the updated package-lock.json.",
)
RULES = (LOCKFILE,)
GROUPS = ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies")


def _directories(lock: Lock, manifest: Table, snapshot: Path) -> list[Path]:
    members = {lock.directory / member for member in lock.members()}
    for pattern in workspace_patterns(manifest):
        if pattern and not PurePosixPath(pattern).is_absolute():
            members.update(
                path for path in lock.directory.glob(pattern) if (path / MANIFEST).is_file()
            )
    return [lock.directory, *sorted(path for path in members if inside(path, ".", snapshot))]


def _dependencies(manifest: Table, locked: Table) -> Iterator[str]:
    for group in GROUPS:
        if group in manifest and table(manifest, group) is None:
            yield f"{group} must be an object mapping package names to dependency specifications"
            continue
        required, recorded = table(manifest, group) or {}, table(locked, group) or {}
        for name in sorted(required.keys() | recorded.keys()):
            wanted, found = required.get(name), recorded.get(name)
            if wanted != found:
                yield (
                    f"{group} requires {name}@{wanted!r}, but package-lock.json records "
                    f"{found!r}; run `npm install` in the lockfile directory and stage "
                    "package-lock.json"
                )


def mismatches(lock: Lock, snapshot: Path) -> Iterator[tuple[Path, str]]:
    """Describe drift in every manifest npm installs, including newly declared workspaces."""
    root = lock.directory / MANIFEST
    try:
        manifest = load_json_object(root)
    except ReportError as error:
        yield root, f"cannot compare the manifest with package-lock.json: {error}"
        return
    for directory in _directories(lock, manifest, snapshot):
        path = directory / MANIFEST
        try:
            current = manifest if directory == lock.directory else load_json_object(path)
        except ReportError as error:
            yield path, f"cannot compare the workspace manifest with package-lock.json: {error}"
            continue
        key = directory.relative_to(lock.directory).as_posix()
        locked = lock.packages.get("" if key == "." else key)
        if locked is None:
            yield (
                path,
                (
                    "the manifest has no package-lock.json entry; run `npm install` in the "
                    "lockfile directory and stage package-lock.json"
                ),
            )
            continue
        yield from ((path, message) for message in _dependencies(current, locked))
