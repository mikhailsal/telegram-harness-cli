"""Locked component environments, synced offline from the uv cache (decision 0009).

Every snapshot gets its own environment per component, built from the candidate's
`uv.lock` by the pinned uv. Checks never download: a package or interpreter that is
not cached is an environment error that names the bootstrap command. `uv sync
--frozen` installs the lockfile as it is, so `uv lock --check` first confirms
that it still matches pyproject.toml. A check's environment compiles its installed
packages once, because its suites never write bytecode and would otherwise compile
them again in every process; the candidate's own source is not compiled.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from quality.adapters.native import fail, failure, run_tool
from quality.policy import Component
from quality.process import Command, ProcessError, run, sanitized

SYNC_TIMEOUT = 600.0
# An online sync downloads interpreters and wheels, which can take long on slow links.
DOWNLOAD_TIMEOUT = 3600.0
# Settings of the user's uv that only choose where caches live.
CACHE_VARIABLES = ("UV_CACHE_DIR",)
TOOL = "uv sync"
LOCK_TOOL = "uv lock --check"
# `uv lock --check` exits 1 when the lockfile needs an update or cannot be resolved offline.
STALE = 1
BOOTSTRAP_HINT = "run `./quality bootstrap` to install the locked packages and interpreter"


class Sync(Enum):
    """How a sync runs: a check's environment, an offline probe, or a download."""

    CHECK = ("--offline", "--compile-bytecode")
    PROBE = ("--offline",)
    DOWNLOAD = ()


@dataclass(frozen=True, slots=True)
class Uv:
    """The pinned uv and the directory that holds its managed interpreters."""

    executable: Path
    pythons: Path

    def environ(
        self, *, project_environment: Path | None = None, base: Mapping[str, str] | None = None
    ) -> dict[str, str]:
        """Return a child environment in which uv uses only managed, installed interpreters."""
        source = os.environ if base is None else base
        extra = {key: source[key] for key in CACHE_VARIABLES if key in source}
        extra.update(
            {
                "UV_PYTHON_INSTALL_DIR": str(self.pythons),
                "UV_PYTHON_PREFERENCE": "only-managed",
                "UV_PYTHON_DOWNLOADS": "never",
                "UV_NO_PROGRESS": "1",
            }
        )
        if project_environment is not None:
            extra["UV_PROJECT_ENVIRONMENT"] = str(project_environment)
        return sanitized(base, extra=extra)


def sync(uv: Uv, project: Path, target: Path, *, label: str, mode: Sync = Sync.PROBE) -> Path:
    """Install the project's locked environment into `target` and return its interpreter."""
    argv = (str(uv.executable), "sync", "--frozen", "--quiet", *mode.value)
    env = uv.environ(project_environment=target)
    timeout = DOWNLOAD_TIMEOUT if mode is Sync.DOWNLOAD else SYNC_TIMEOUT
    try:
        completed = run(Command(argv, cwd=project, timeout=timeout, env=env))
    except ProcessError as error:
        raise failure(TOOL, f"{label}: {error}; {BOOTSTRAP_HINT}") from error
    if not completed.ok:
        detail = completed.stderr_text().strip() or f"exit status {completed.returncode}"
        fail(TOOL, f"{label}: {detail}; {BOOTSTRAP_HINT}")
    python = target / "bin" / "python"
    if not python.is_file():
        fail(TOOL, f"{label}: no interpreter at {python}")
    return python


def stale_lock(uv: Uv, project: Path) -> str | None:
    """Return why the project's uv.lock does not match pyproject.toml, or None when it does."""
    argv = (str(uv.executable), "lock", "--check", "--offline", "--quiet")
    command = Command(argv, cwd=project, timeout=SYNC_TIMEOUT, env=uv.environ())
    completed = run_tool(command, name=LOCK_TOOL, accepted={0, STALE})
    if completed.returncode == 0:
        return None
    return completed.stderr_text().strip() or "uv.lock needs to be updated"


@dataclass(slots=True)
class Environments:
    """One environment per component and snapshot work area, synced on first use."""

    uv: Uv
    _ready: dict[Path, Path] = field(default_factory=dict[Path, Path])

    def location(self, component: Component, work: Path) -> Path:
        """Return where the component's environment lives inside a work area."""
        return work / "python-env" / component.id

    def python(self, component: Component, snapshot: Path, work: Path) -> Path:
        """Return the interpreter of the component's environment, syncing it if needed."""
        target = self.location(component, work)
        if target not in self._ready:
            project = snapshot / component.root
            self._ready[target] = sync(
                self.uv, project, target, label=component.id, mode=Sync.CHECK
            )
        return self._ready[target]
