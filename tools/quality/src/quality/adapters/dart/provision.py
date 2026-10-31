"""What `bootstrap` downloads for a Dart component and what `doctor` probes offline.

Bootstrap installs the pinned SDK, then resolves the component's lockfile once
with network access in a throwaway copy of the package manifests, which fills
the shared pub cache. Only the lock directory's pubspec.yaml and pubspec.lock
and the pubspec.yaml files of its workspace members and path dependencies
inside the repository are copied, so the resolution reads no other file of the
working tree. The helper's own lockfile is resolved and the helper compiled in
the same step. Doctor locates the SDK without downloading it and repeats both
offline (decision 0010).
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from quality.adapters.dart.config import check_config
from quality.adapters.dart.environment import Sdk, located, pub_get
from quality.adapters.dart.helper import Helper
from quality.adapters.dart.lockfile import LOCKFILE, PUBSPEC, Lock, members
from quality.diagnostics import Diagnostic
from quality.policy import Component


def copy_manifests(lock: Lock, root: Path, target: Path) -> Path:
    """Copy what `dart pub get` reads below `root` into `target`; return the lock directory."""
    base = root.resolve()
    directory = lock.directory.resolve()
    packages = [*members(directory, base), *((directory / path) for _, path in lock.paths())]
    sources = [directory / LOCKFILE, directory / PUBSPEC]
    sources += [package.resolve() / PUBSPEC for package in packages]
    for source in sources:
        if source.is_relative_to(base) and source.is_file():
            copy = target / source.relative_to(base)
            copy.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, copy)
    return target / directory.relative_to(base)


@dataclass(slots=True)
class DartSetup:
    """Provisions and probes component packages and the helper with the pinned SDK."""

    locate: Callable[[], Path]
    install: Callable[[], Path]
    cache: Path
    pins: Mapping[str, str]
    helper: Helper
    _done: set[tuple[bool, Path]] = field(default_factory=set[tuple[bool, Path]])

    def _resolved(self, component: Component, root: Path, *, offline: bool) -> list[Diagnostic]:
        settings, report = check_config(component, root, self.pins)
        if settings is None or report.found:
            return report.found
        sdk = Sdk(located(self.locate if offline else self.install), self.cache)
        lock = settings.lock
        if (offline, lock.directory) not in self._done:
            with tempfile.TemporaryDirectory(prefix=f"quality-{component.id}-") as scratch:
                copied = copy_manifests(lock, root, Path(scratch) / "packages")
                pub_get(sdk, copied, Path(scratch) / "home", label=component.id, offline=offline)
            self._done.add((offline, lock.directory))
        self.helper.program(sdk, offline=offline)
        return []

    def provision(self, component: Component, root: Path) -> list[Diagnostic]:
        """Install the pinned SDK, cache the locked packages, and compile the helper."""
        return self._resolved(component, root, offline=False)

    def probe(self, component: Component, root: Path) -> list[Diagnostic]:
        """Resolve the locked packages offline in a throwaway directory; compile the helper."""
        return self._resolved(component, root, offline=True)
