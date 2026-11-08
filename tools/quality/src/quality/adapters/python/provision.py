"""What `bootstrap` downloads for a Python component and what `doctor` probes offline.

Bootstrap installs the pinned interpreter and syncs the locked packages once with
network access into a throwaway environment, which fills uv's cache. Checks then
sync offline from that cache (decision 0010).
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from quality.adapters.native import fail, failure
from quality.adapters.python.config import check_config
from quality.adapters.python.environment import DOWNLOAD_TIMEOUT, Sync, Uv, sync
from quality.diagnostics import Diagnostic
from quality.policy import Component
from quality.process import Command, ProcessError, run

TOOL = "uv python install"


@dataclass(frozen=True, slots=True)
class PythonSetup:
    """Provisions and probes component environments with the pinned uv."""

    uv: Uv

    def _install_python(self, directory: Path, label: str) -> None:
        version = (directory / ".python-version").read_text(encoding="utf-8").strip()
        env = self.uv.environ()
        env["UV_PYTHON_DOWNLOADS"] = "automatic"
        # --no-bin keeps the interpreter out of the user's ~/.local/bin.
        argv = (str(self.uv.executable), "python", "install", "--no-bin", version)
        try:
            completed = run(Command(argv, cwd=directory, timeout=DOWNLOAD_TIMEOUT, env=env))
        except ProcessError as error:
            raise failure(TOOL, f"{label}: {error}") from error
        if not completed.ok:
            fail(TOOL, f"{label}: {completed.stderr_text().strip() or completed.returncode}")

    def _synced(self, component: Component, root: Path, mode: Sync) -> list[Diagnostic]:
        settings, report = check_config(component, root)
        if settings is None or report.found:
            return report.found
        directory = root / component.root
        if mode is Sync.DOWNLOAD:
            self._install_python(directory, component.id)
        with tempfile.TemporaryDirectory(prefix=f"quality-{component.id}-") as scratch:
            target = Path(scratch) / "environment"
            sync(self.uv, directory, target, label=component.id, mode=mode)
        return []

    def provision(self, component: Component, root: Path) -> list[Diagnostic]:
        """Install the pinned interpreter and cache the locked packages."""
        return self._synced(component, root, Sync.DOWNLOAD)

    def probe(self, component: Component, root: Path) -> list[Diagnostic]:
        """Sync the locked environment offline into a throwaway directory."""
        return self._synced(component, root, Sync.PROBE)
