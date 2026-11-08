"""What `bootstrap` downloads for a PHP component and what `doctor` probes offline.

Bootstrap installs the pinned Composer and probes the system PHP and Xdebug,
then installs the component's lockfile once with network access in a
throwaway copy of composer.json and composer.lock, which fills the shared
Composer cache. Only those two files and the composer.json files of the
packages the lockfile installs from paths inside the repository are copied,
so the installation reads no other file of the working tree. The helper is
installed in the same step. Doctor locates Composer without downloading it and
repeats both offline (decision 0010).
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from quality.adapters.php.composer import install, install_command
from quality.adapters.php.config import check_config
from quality.adapters.php.helper import Helper
from quality.adapters.php.lockfile import LOCKFILE, MANIFEST, Lock
from quality.adapters.php.runtime import Requirements, located, probed
from quality.diagnostics import Diagnostic
from quality.policy import Component


def copy_manifests(lock: Lock, root: Path, target: Path) -> Path:
    """Copy what `composer install` reads below `root` into `target`; return the copy."""
    base = root.resolve()
    directory = lock.directory.resolve()
    packages = [(directory / path).resolve() for _, path in lock.paths()]
    sources = [directory / MANIFEST, directory / LOCKFILE]
    sources += [package / MANIFEST for package in packages]
    for source in sources:
        if source.is_relative_to(base) and source.is_file():
            copy = target / source.relative_to(base)
            copy.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, copy)
    return target / directory.relative_to(base)


@dataclass(slots=True)
class PhpSetup:
    """Provisions and probes component packages and the helper with the pinned Composer."""

    locate_php: Callable[[], Path]
    locate: Callable[[], Path]
    install: Callable[[], Path]
    cache: Path
    required: Requirements
    pins: Mapping[str, str]
    helper: Helper
    _done: set[tuple[bool, Path]] = field(default_factory=set[tuple[bool, Path]])

    def _installed(self, component: Component, root: Path, *, offline: bool) -> list[Diagnostic]:
        settings, report = check_config(component, root, self.pins)
        if settings is None or report.found:
            return report.found
        composer = located(self.locate if offline else self.install)
        php = probed(self.locate_php(), composer, self.cache, self.required)
        lock = settings.lock
        if (offline, lock.directory) not in self._done:
            with tempfile.TemporaryDirectory(prefix=f"quality-{component.id}-") as scratch:
                copied = copy_manifests(lock, root, Path(scratch) / "packages")
                home = Path(scratch) / "home"
                install(install_command(php, copied, home, online=not offline), component.id)
            self._done.add((offline, lock.directory))
        self.helper.program(php, offline=offline)
        return []

    def provision(self, component: Component, root: Path) -> list[Diagnostic]:
        """Install the pinned Composer, cache the locked packages, and install the helper."""
        return self._installed(component, root, offline=False)

    def probe(self, component: Component, root: Path) -> list[Diagnostic]:
        """Install the locked packages offline in a throwaway directory; install the helper."""
        return self._installed(component, root, offline=True)
