"""What `bootstrap` downloads for a TypeScript component and what `doctor` probes offline.

Bootstrap installs the pinned Node, then runs the component's `npm ci` once with
network access in a throwaway copy of the package manifests, which fills the
shared npm cache. Only the lockfile and the package.json files of the root and
its workspace members are copied: the project's `.npmrc` is not, so the install
uses the public registry and no credentials. Doctor locates Node without
downloading it and repeats the install offline (decision 0010). For a browser
component, bootstrap also installs the pinned Chromium and doctor locates it;
both check that it starts and that the installed Playwright expects it.
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from quality.adapters.typescript.chromium import VERSION, Chromium, expected_version
from quality.adapters.typescript.config import check_config
from quality.adapters.typescript.environment import Node, npm_ci
from quality.adapters.typescript.lockfile import LOCKFILE, MANIFEST, Lock
from quality.boundedio import ReportError
from quality.diagnostics import Diagnostic
from quality.policy import Component


def copy_manifests(lock: Lock, root: Path, target: Path) -> Path:
    """Copy what `npm ci` reads below `root` into `target`; return the copied lock directory."""
    base = root.resolve()
    directory = lock.directory.resolve()
    sources = [directory / LOCKFILE, directory / MANIFEST]
    sources += [(directory / member).resolve() / MANIFEST for member in lock.members()]
    for source in sources:
        if source.is_file():
            copy = target / source.relative_to(base)
            copy.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, copy)
    return target / directory.relative_to(base)


@dataclass(slots=True)
class TypeScriptSetup:
    """Provisions and probes component packages with the pinned Node, and the browser."""

    locate: Callable[[], Path]
    install: Callable[[], Path]
    cache: Path
    pins: Mapping[str, str]
    chromium: Chromium
    _done: set[tuple[bool, Path, str]] = field(default_factory=set[tuple[bool, Path, str]])

    def _installed(self, component: Component, root: Path, *, offline: bool) -> list[Diagnostic]:
        settings, report = check_config(component, root, self.pins)
        if settings is None or report.found:
            return report.found
        node = Node(self.locate() if offline else self.install(), self.cache)
        lock = settings.lock
        # A browser component's Playwright is read from its own install of the lockfile.
        done = (offline, lock.directory, component.root if component.in_browser else "")
        found: list[Diagnostic] = []
        if done not in self._done:
            with tempfile.TemporaryDirectory(prefix=f"quality-{component.id}-") as scratch:
                copied = copy_manifests(lock, root, Path(scratch) / "packages")
                npm_ci(node, copied, Path(scratch) / "home", label=component.id, offline=offline)
                if component.in_browser:
                    found = self._browser(component, root, lock, copied, offline=offline)
            self._done.add(done)
        return found

    def _browser(
        self, component: Component, root: Path, lock: Lock, installed: Path, *, offline: bool
    ) -> list[Diagnostic]:
        try:
            expected = expected_version(lock, root / component.root, installed)
        except ReportError as error:
            message = f"cannot tell which Chromium the locked Playwright expects: {error}"
            return [VERSION.diagnostic(message, component=component.id)]
        return self.chromium.ready(component.id, expected, offline=offline)

    def provision(self, component: Component, root: Path) -> list[Diagnostic]:
        """Install the pinned Node, cache the locked packages, and install the browser."""
        return self._installed(component, root, offline=False)

    def probe(self, component: Component, root: Path) -> list[Diagnostic]:
        """Install the locked packages offline into a throwaway directory."""
        return self._installed(component, root, offline=True)
