"""The composer.lock of a PHP component and the packages it locks (decision 0013).

Composer installs a component from the composer.lock beside its composer.json.
The formatter, the analyzer, the test runner, and the library that writes the
coverage reports are the component's own locked packages, so every package of
`[packages.composer]` must be locked at exactly the pinned version, from a zip
archive downloaded over HTTPS; Composer writes a tag such as `v3.95.27` with
its leading `v`, which is ignored.

Every other locked archive and source must come over HTTPS too, and a package that
Composer reads from a path must stay inside the repository, so an installation
reads nothing from outside the snapshot.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from quality.adapters.native import fail
from quality.adapters.settings import Report, Table, inside, load_json_object, table
from quality.boundedio import ReportError

LOCKFILE = "composer.lock"
MANIFEST = "composer.json"
REQUIRED = (
    "phpunit/phpunit",
    "phpunit/php-code-coverage",
    "phpstan/phpstan",
    "friendsofphp/php-cs-fixer",
)
SECTIONS = ("packages", "packages-dev")
HTTPS = "https://"
# Composer falls back from the archive to the source when the archive fails.
ORIGINS = ("dist", "source")


@dataclass(frozen=True, slots=True)
class Lock:
    """A parsed composer.lock: its directory and its packages by name."""

    directory: Path
    packages: Mapping[str, Table]

    def paths(self) -> list[tuple[str, str]]:
        """Return each package that Composer reads from a path, with the path as locked."""
        found: list[tuple[str, str]] = []
        for name, entry in self.packages.items():
            dist = table(entry, "dist") or {}
            url = dist.get("url")
            if dist.get("type") == "path" and isinstance(url, str):
                found.append((name, url))
        return found


def load_lock(directory: Path) -> Lock:
    """Read a lockfile; raise ReportError when Composer could not install from it."""
    document = load_json_object(directory / LOCKFILE)
    packages: dict[str, Table] = {}
    for section in SECTIONS:
        entries = document.get(section, [])
        if not isinstance(entries, list):
            msg = f"'{section}' is not a list"
            raise ReportError(msg)
        for entry in cast(list[object], entries):
            name = cast(Table, entry).get("name") if isinstance(entry, Mapping) else None
            if not isinstance(name, str):
                msg = f"an entry of '{section}' has no package name"
                raise ReportError(msg)
            packages[name] = cast(Table, entry)
    return Lock(directory, packages)


def _version(entry: Table) -> object:
    version = entry.get("version")
    return version.removeprefix("v") if isinstance(version, str) else version


def _pins(lock: Lock, pins: Mapping[str, str], add: Report) -> None:
    for package in REQUIRED:
        pin = pins.get(package)
        if pin is None:
            fail("php", f"toolchains.toml pins no version of {package}")
        entry = lock.packages.get(package)
        if entry is None:
            add(LOCKFILE, f"the lockfile does not lock {package}; require `{package}: {pin}`")
            continue
        dist = table(entry, "dist") or {}
        url = dist.get("url")
        archived = dist.get("type") == "zip" and isinstance(url, str) and url.startswith(HTTPS)
        if _version(entry) != pin or not archived:
            add(
                LOCKFILE,
                f"{package} is locked at {entry.get('version')!r} from {url!r}; the profile "
                f"pins {pin} from a zip archive over HTTPS",
            )


def _remote(package: str, entry: Table, add: Report) -> None:
    origins = [table(entry, key) or {} for key in ORIGINS]
    urls = [origin.get("url") for origin in origins if origin.get("type") != "path"]
    for url in dict.fromkeys(url for url in urls if isinstance(url, str)):
        if not url.startswith(HTTPS):
            add(LOCKFILE, f"{package} is downloaded from {url!r}, not over HTTPS")


def _sources(lock: Lock, snapshot: Path, add: Report) -> None:
    for package, entry in lock.packages.items():
        _remote(package, entry, add)
    for package, path in lock.paths():
        if not inside(lock.directory, path, snapshot):
            add(LOCKFILE, f"{package} is installed from {path!r}, outside the repository")


def check_lock(
    directory: Path, snapshot: Path, pins: Mapping[str, str], add: Report
) -> Lock | None:
    """Check the component's lockfile; return it when Composer can install from it."""
    if not (directory / LOCKFILE).is_file():
        add(LOCKFILE, f"no {LOCKFILE} beside composer.json; run `composer update` and commit it")
        return None
    try:
        lock = load_lock(directory)
    except ReportError as error:
        add(LOCKFILE, f"the lockfile is unusable: {error}")
        return None
    _pins(lock, pins, add)
    _sources(lock, snapshot, add)
    return lock
