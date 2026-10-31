"""The native configuration of a PHP component, checked before its tools run.

The profile runs the component's locked PHP CS Fixer, PHPStan, and PHPUnit
with the gate's own settings, so it reads none of the component's
`.php-cs-fixer.php`, `phpstan.neon`, or `phpunit.xml` files (decision 0013).
What it does read is composer.json: the PSR-4 prefixes of `autoload` and
`autoload-dev`, which map class names to files for the import graph, and the
vendor directory, which must be `vendor/` because the generated test bootstrap
loads `vendor/autoload.php`. Composer itself validates the manifest and the
lockfile's freshness when the component is prepared.
"""

from __future__ import annotations

import posixpath
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import cast

from quality.adapters.php.lockfile import MANIFEST, Lock, check_lock
from quality.adapters.settings import Table, inside, load_json_object, table
from quality.boundedio import ReportError
from quality.diagnostics import Diagnostic, Location, Rule
from quality.policy import Component

CONFIG = Rule(
    id="php.config",
    title="PHP configuration is missing, unpinned, or invalid",
    requirement="R04",
    explanation=(
        "A PHP component needs a composer.json that Composer validates, with its vendor "
        "directory at `vendor/` and PSR-4 directories inside the repository, and a current "
        "composer.lock beside it that locks the pinned phpunit/phpunit, "
        "phpunit/php-code-coverage, phpstan/phpstan, and friendsofphp/php-cs-fixer from zip "
        "archives over HTTPS. Packages installed from a "
        "path stay inside the repository. The candidate may not commit `vendor/`, which "
        "Composer installs from the lockfile."
    ),
    remedy=(
        "Fix the named file; require the pinned versions exactly in require-dev and run "
        "`composer update` with the pinned Composer."
    ),
)
RULES = (CONFIG,)

VENDOR = "vendor"
AUTOLOAD = ("autoload", "autoload-dev")


def php_files(files: tuple[str, ...]) -> tuple[str, ...]:
    """Return the PHP files of a list."""
    return tuple(path for path in files if PurePosixPath(path).suffix == ".php")


@dataclass(frozen=True, slots=True)
class Prefix:
    """A PSR-4 namespace prefix and its directories, relative to the component directory."""

    prefix: str
    directories: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PhpConfig:
    """The PSR-4 prefixes of a component and the lockfile Composer installs from."""

    autoload: tuple[Prefix, ...]
    lock: Lock


@dataclass(slots=True)
class Findings:
    """Problems found in one component's configuration files."""

    component: Component
    found: list[Diagnostic] = field(default_factory=list[Diagnostic])

    def add(self, file: str, message: str) -> None:
        """Record a problem in a file relative to the component directory."""
        location = Location(posixpath.normpath(posixpath.join(self.component.root, file)))
        self.found.append(
            CONFIG.diagnostic(message, location=location, component=self.component.id)
        )


def _directories(value: object) -> list[str] | None:
    if isinstance(value, str):
        return [value]
    items = cast(list[object], value) if isinstance(value, list) else None
    if items is None or not all(isinstance(item, str) for item in items):
        return None
    return cast(list[str], items)


def _prefixes(manifest: Table, directory: Path, snapshot: Path, report: Findings) -> list[Prefix]:
    found: list[Prefix] = []
    for section in AUTOLOAD:
        mapping = table(manifest, section, "psr-4") or {}
        for prefix, value in mapping.items():
            directories = _directories(value)
            if directories is None:
                report.add(MANIFEST, f"`{section}.psr-4.{prefix}` must name directories")
                continue
            outside = [path for path in directories if not inside(directory, path, snapshot)]
            for path in outside:
                report.add(
                    MANIFEST, f"`{section}.psr-4.{prefix}` names {path!r}, outside the repository"
                )
            if not outside:
                found.append(Prefix(prefix, tuple(directories)))
    return found


def check_config(
    component: Component, snapshot: Path, pins: Mapping[str, str]
) -> tuple[PhpConfig | None, Findings]:
    """Check a component's configuration; without a manifest and a lockfile nothing can run."""
    directory = snapshot / component.root
    report = Findings(component)
    try:
        manifest = load_json_object(directory / MANIFEST)
    except ReportError as error:
        report.add(MANIFEST, f"a composer.json holding a JSON object is required ({error})")
        return None, report
    vendor = (table(manifest, "config") or {}).get("vendor-dir", VENDOR)
    if vendor != VENDOR:
        report.add(
            MANIFEST, f"`config.vendor-dir` is {vendor!r}; the profile installs into `vendor`"
        )
    autoload = _prefixes(manifest, directory, snapshot, report)
    lock = check_lock(directory, snapshot, pins, report.add)
    if lock is None:
        return None, report
    return PhpConfig(tuple(autoload), lock), report
