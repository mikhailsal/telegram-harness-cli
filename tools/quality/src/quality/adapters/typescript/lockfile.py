"""The npm lockfile of a TypeScript component and the tool versions it locks.

npm installs a workspace from the lockfile at the workspace root, so a
component's lockfile is in its own directory or in the nearest ancestor whose
package.json lists the component in `workspaces`. A tool counts as locked at the
version Node would load from the component directory: the nearest
`node_modules/<name>` entry on the way up to the lockfile wins, so a version
nested for the component cannot hide behind a pinned hoisted one.

Entries that npm would install from a path, links, and workspace folders must
stay inside the repository, so an install reads nothing from outside the
snapshot.
"""

from __future__ import annotations

import posixpath
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import cast

from quality.adapters.native import fail
from quality.adapters.settings import Report, Table, inside, load_json_object, strings, table
from quality.boundedio import ReportError

LOCKFILE = "package-lock.json"
MANIFEST = "package.json"
NODE_MODULES = "node_modules"
LOCK_VERSIONS = frozenset({2, 3})
# Every component locks these at the pinned versions; `@eslint/js` only when it uses it.
REQUIRED = (
    "typescript",
    "eslint",
    "typescript-eslint",
    "prettier",
    "vitest",
    "@vitest/coverage-v8",
)
OPTIONAL = ("@eslint/js",)
# A browser component also locks these (decision 0017); `globals` only when it uses it.
BROWSER_REQUIRED = (
    "react",
    "react-dom",
    "@types/react",
    "@types/react-dom",
    "vite",
    "@vitejs/plugin-react",
    "jsdom",
    "@testing-library/react",
    "@testing-library/dom",
    "@testing-library/user-event",
    "eslint-plugin-react-hooks",
    "eslint-plugin-jsx-a11y-x",
    "stylelint",
    "stylelint-config-standard",
    "stylelint-config-css-modules",
    "@playwright/test",
    "@axe-core/playwright",
)
BROWSER_OPTIONAL = ("globals",)


def workspace_patterns(manifest: Table) -> list[str]:
    """Return the `workspaces` globs of a package.json, in either of npm's two forms."""
    value = manifest.get("workspaces")
    packages = table(manifest, "workspaces")
    found = strings(packages.get("packages")) if packages is not None else strings(value)
    return [pattern.removeprefix("./").rstrip("/") for pattern in found]


def _lists(ancestor: Path, directory: Path) -> bool:
    try:
        manifest = load_json_object(ancestor / MANIFEST)
    except ReportError:
        return False
    relative = PurePosixPath(directory.relative_to(ancestor).as_posix())
    return any(relative.full_match(pattern) for pattern in workspace_patterns(manifest) if pattern)


def lock_directory(directory: Path, snapshot: Path) -> Path | None:
    """Return the directory of the lockfile npm installs the component from, if any."""
    if (directory / LOCKFILE).is_file():
        return directory
    for ancestor in (path for path in directory.parents if path.is_relative_to(snapshot)):
        if (ancestor / MANIFEST).is_file() and _lists(ancestor, directory):
            return ancestor if (ancestor / LOCKFILE).is_file() else None
    return None


@dataclass(frozen=True, slots=True)
class Lock:
    """A parsed lockfile: its directory and its `packages` entries by install path."""

    directory: Path
    packages: Mapping[str, Table]

    def members(self) -> tuple[str, ...]:
        """Return the workspace folders the lockfile installs, relative to its directory."""
        return tuple(key for key in self.packages if key and NODE_MODULES not in key.split("/"))

    def resolve(self, name: str, directory: Path) -> tuple[str, Table] | None:
        """Return the entry Node would load for `name` from `directory`, with its key."""
        start = PurePosixPath(directory.relative_to(self.directory).as_posix())
        for base in (start, *start.parents):
            prefix = "" if str(base) == "." else f"{base}/"
            key = f"{prefix}{NODE_MODULES}/{name}"
            entry = self.packages.get(key)
            if entry is not None:
                return key, entry
        return None


def load_lock(directory: Path) -> Lock:
    """Read a lockfile; raise ReportError when npm could not install from it."""
    data = load_json_object(directory / LOCKFILE)
    version = data.get("lockfileVersion")
    if version not in LOCK_VERSIONS:
        msg = f"lockfileVersion {version!r} is not 2 or 3; run `npm install` with npm 7 or later"
        raise ReportError(msg)
    packages = table(data, "packages")
    if packages is None:
        msg = "the lockfile has no 'packages' table"
        raise ReportError(msg)
    entries = {
        key: cast(Table, value) for key, value in packages.items() if isinstance(value, Mapping)
    }
    return Lock(directory, entries)


def _sources(lock: Lock, snapshot: Path) -> list[tuple[str, str]]:
    """Return the entries npm would read from a path outside the snapshot."""
    outside: list[tuple[str, str]] = []
    for key, entry in lock.packages.items():
        resolved = entry.get("resolved")
        paths = [key] if key and NODE_MODULES not in key.split("/") else []
        if isinstance(resolved, str) and (entry.get("link") is True or "://" not in resolved):
            paths.append(resolved.removeprefix("file:"))
        outside.extend((key, path) for path in paths if not inside(lock.directory, path, snapshot))
    return outside


def _version(lock: Lock, name: str, directory: Path) -> tuple[str, object] | None:
    found = lock.resolve(name, directory)
    if found is None:
        return None
    key, entry = found
    return key, None if entry.get("link") is True else entry.get("version")


def _unpinned(
    lock: Lock, directory: Path, pins: Mapping[str, str], *, browser: bool
) -> Iterator[str]:
    """Describe each profile package the lockfile does not lock at its pin."""
    required = (*REQUIRED, *BROWSER_REQUIRED) if browser else REQUIRED
    optional = (*OPTIONAL, *BROWSER_OPTIONAL) if browser else OPTIONAL
    for package in (*required, *optional):
        found = _version(lock, package, directory)
        pin = pins.get(package)
        if pin is None:
            fail("typescript", f"toolchains.toml pins no version of {package}")
        if found is None:
            if package in required:
                yield f"the lockfile does not lock {package}; add {package}@{pin}"
        elif found[1] != pin:
            key, version = found
            yield f"{key} is locked at {version!r}; the profile pins {package} {pin}"


def check_lock(
    directory: Path, snapshot: Path, pins: Mapping[str, str], add: Report, *, browser: bool = False
) -> Lock | None:
    """Check the component's lockfile; return it when npm can install from it.

    A `browser` component must also lock the browser packages at their pins.
    """
    where = lock_directory(directory, snapshot)
    if where is None:
        message = (
            f"no {LOCKFILE} in the component or in a workspace root that lists it; "
            "run `npm install`"
        )
        add(LOCKFILE, message)
        return None
    name = posixpath.relpath((where / LOCKFILE).as_posix(), directory.as_posix())
    try:
        lock = load_lock(where)
    except ReportError as error:
        add(name, f"the lockfile is unusable: {error}")
        return None
    for message in _unpinned(lock, directory, pins, browser=browser):
        add(name, message)
    for key, path in _sources(lock, snapshot):
        add(name, f"{key or 'the root package'} is installed from {path!r}, outside the repository")
    return lock
