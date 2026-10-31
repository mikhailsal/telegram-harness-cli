"""The pubspec.lock of a Dart component and the package versions it locks.

pub resolves a workspace from the lockfile at the workspace root, so a
component whose pubspec.yaml says `resolution: workspace` is locked by the
nearest ancestor inside the repository whose pubspec.yaml lists it in
`workspace`, by a relative path or a glob; a workspace root that is itself a
member defers to its own root. Any other component has its lockfile in its own
directory. The test runner and the coverage formatter run inside the
component's package configuration, so every package of `[packages.pub]` must be
locked at exactly the pinned version, from a package repository.

Packages that pub would read from a path must stay inside the repository, so a
resolution reads nothing from outside the snapshot; pub itself keeps workspace
members below their root. Packages from an SDK are Flutter's, which the profile
does not support.
"""

from __future__ import annotations

import posixpath
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import cast

from quality.adapters.native import fail
from quality.adapters.settings import Report, Table, inside, load_yaml_mapping, strings, table
from quality.boundedio import ReportError

LOCKFILE = "pubspec.lock"
PUBSPEC = "pubspec.yaml"
REQUIRED = ("test", "coverage", "lints")
VM_ONLY = "the Dart profile supports the Dart VM only"


def workspace_patterns(pubspec: Table) -> list[str]:
    """Return the `workspace` entries of a pubspec, without `./` and trailing slashes."""
    return [entry.removeprefix("./").rstrip("/") for entry in strings(pubspec.get("workspace"))]


def _root(directory: Path, snapshot: Path) -> tuple[Path, Table] | None:
    """Return the nearest ancestor in the snapshot whose pubspec lists `directory`, with it."""
    for ancestor in (path for path in directory.parents if path.is_relative_to(snapshot)):
        try:
            pubspec = load_yaml_mapping(ancestor / PUBSPEC)
        except ReportError:
            continue
        relative = PurePosixPath(directory.relative_to(ancestor).as_posix())
        if any(relative.full_match(entry) for entry in workspace_patterns(pubspec) if entry):
            return ancestor, pubspec
    return None


def lock_directory(directory: Path, snapshot: Path, pubspec: Table) -> Path | None:
    """Return the directory of the lockfile pub resolves the component from, if any."""
    current, settings = directory, pubspec
    while settings.get("resolution") == "workspace":
        found = _root(current, snapshot)
        if found is None:
            return None
        current, settings = found
    return current if (current / LOCKFILE).is_file() else None


def _listed(directory: Path) -> list[Path]:
    """Return the paths the `workspace` entries of a pubspec match; none for an unusable one."""
    try:
        pubspec = load_yaml_mapping(directory / PUBSPEC)
    except ReportError:
        return []
    entries = (entry for entry in workspace_patterns(pubspec) if entry and entry[0] != "/")
    return sorted(path for entry in entries for path in directory.glob(entry))


def members(directory: Path, top: Path) -> list[Path]:
    """Return the workspace members below a workspace root, nested ones included, inside `top`."""
    found: list[Path] = []
    pending = [directory]
    while pending:
        for member in _listed(pending.pop()):
            if member in found or not member.resolve().is_relative_to(top.resolve()):
                continue
            if (member / PUBSPEC).is_file():
                found.append(member)
                pending.append(member)
    return found


@dataclass(frozen=True, slots=True)
class Lock:
    """A parsed lockfile: its directory and its `packages` entries by name."""

    directory: Path
    packages: Mapping[str, Table]

    def paths(self) -> list[tuple[str, str]]:
        """Return each package that pub reads from a path, with the path as locked."""
        found: list[tuple[str, str]] = []
        for name, entry in self.packages.items():
            description = table(entry, "description") or {}
            path = description.get("path")
            if entry.get("source") == "path" and isinstance(path, str):
                found.append((name, path))
        return found


def load_lock(directory: Path) -> Lock:
    """Read a lockfile; raise ReportError when pub could not resolve from it."""
    packages = table(load_yaml_mapping(directory / LOCKFILE), "packages")
    if packages is None:
        msg = "the lockfile has no 'packages' mapping"
        raise ReportError(msg)
    entries = {
        str(name): cast(Table, entry)
        for name, entry in packages.items()
        if isinstance(entry, Mapping)
    }
    return Lock(directory, entries)


def _pins(lock: Lock, pins: Mapping[str, str], name: str, add: Report) -> None:
    for package in REQUIRED:
        pin = pins.get(package)
        if pin is None:
            fail("dart", f"toolchains.toml pins no version of {package}")
        entry = lock.packages.get(package)
        if entry is None:
            add(name, f"the lockfile does not lock {package}; add `{package}: {pin}`")
        elif entry.get("version") != pin or entry.get("source") != "hosted":
            version, source = entry.get("version"), entry.get("source")
            add(name, f"{package} is locked at {version!r} from {source!r}; the profile pins {pin}")


def _sources(lock: Lock, snapshot: Path, name: str, add: Report) -> None:
    for package, entry in lock.packages.items():
        if entry.get("source") == "sdk":
            add(name, f"{package} comes from the {entry.get('description')} SDK; {VM_ONLY}")
    for package, path in lock.paths():
        if not inside(lock.directory, path, snapshot):
            add(name, f"{package} is resolved from {path!r}, outside the repository")


def check_lock(
    directory: Path, snapshot: Path, pubspec: Table, pins: Mapping[str, str], add: Report
) -> Lock | None:
    """Check the component's lockfile; return it when pub can resolve from it."""
    where = lock_directory(directory, snapshot, pubspec)
    if where is None:
        message = (
            f"no {LOCKFILE} in the component or in a workspace root that lists it; "
            "run `dart pub get`"
        )
        add(LOCKFILE, message)
        return None
    name = posixpath.relpath((where / LOCKFILE).as_posix(), directory.as_posix())
    try:
        lock = load_lock(where)
    except ReportError as error:
        add(name, f"the lockfile is unusable: {error}")
        return None
    _pins(lock, pins, name, add)
    _sources(lock, snapshot, name, add)
    return lock
