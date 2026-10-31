"""Install and inspect the managed hooks without ever touching a hook the user owns."""

from __future__ import annotations

import contextlib
import os
import stat
import tempfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from quality.diagnostics import Diagnostic, Kind, Rule, Severity
from quality.gitio import GitioError, Repository
from quality.gitio.errors import GIT_FAILED
from quality.hooks.scripts import HookName, is_managed, script

CONFLICT = Rule(
    id="hooks.conflict",
    title="An existing hook is not managed by quality",
    requirement="R10",
    explanation=(
        "Bootstrap installs its hooks only where no foreign hook exists, and only in the "
        "repository's own hooks directory. A hook it did not write, or a core.hooksPath "
        "setting, is never overwritten, moved, or deleted."
    ),
    remedy=(
        "Rename the existing hook to '<name>.local' next to it (the managed hook runs it "
        "first), or unset core.hooksPath, then run './quality bootstrap' again."
    ),
    kind=Kind.ENVIRONMENT,
)
MISSING = Rule(
    id="hooks.missing",
    title="A managed hook is not installed or is outdated",
    requirement="R02",
    explanation=(
        "Without the current managed pre-commit hook, commits are not checked locally. "
        "CI still runs the same gate."
    ),
    remedy="Run './quality bootstrap' to install the current hooks.",
    kind=Kind.ENVIRONMENT,
)
RULES = (CONFLICT, MISSING)

HOOK_LIMIT = 1024 * 1024
EXECUTABLE = 0o755


class HookStatus(StrEnum):
    """The state of one hook file."""

    MISSING = "missing"
    CURRENT = "current"
    OUTDATED = "outdated"
    FOREIGN = "foreign"


@dataclass(frozen=True, slots=True)
class HookState:
    """Where a hook lives and whether it is the current managed version."""

    name: HookName
    path: Path
    status: HookStatus


class HookConflictError(Exception):
    """A hook cannot be installed without overwriting something the user owns."""

    def __init__(self, diagnostic: Diagnostic) -> None:
        super().__init__(diagnostic.message)
        self.diagnostic = diagnostic


def _hooks_path(repo: Repository) -> str | None:
    result = repo.try_git("config", "--get", "core.hooksPath")
    if result.returncode == 1:
        return None
    if not result.ok:
        detail = result.stderr_text().strip() or f"exit {result.returncode}"
        raise GitioError(GIT_FAILED.diagnostic(f"git config failed: {detail}"))
    return result.stdout_text().strip() or None


def hooks_directory(repo: Repository) -> Path:
    """Return the repository's own hooks directory; a core.hooksPath setting is a conflict."""
    configured = _hooks_path(repo)
    if configured is not None:
        message = (
            f"core.hooksPath is set to '{configured}'; managed hooks are installed only in "
            "the repository's own hooks directory. Add './quality check --staged' to that "
            "hook manager, or unset core.hooksPath and run './quality bootstrap' again"
        )
        raise HookConflictError(CONFLICT.diagnostic(message))
    raw = repo.git("rev-parse", "--git-path", "hooks").stdout_text().strip()
    return repo.worktree / raw


def _status(name: HookName, path: Path) -> HookStatus:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return HookStatus.MISSING
    if not stat.S_ISREG(info.st_mode) or info.st_size > HOOK_LIMIT:
        return HookStatus.FOREIGN
    content = path.read_bytes()
    if not is_managed(name, content):
        return HookStatus.FOREIGN
    executable = stat.S_IMODE(info.st_mode) & stat.S_IXUSR
    if content == script(name).encode() and executable:
        return HookStatus.CURRENT
    return HookStatus.OUTDATED


def inspect(repo: Repository, name: HookName) -> HookState:
    """Report the state of one hook; nothing is modified."""
    path = hooks_directory(repo) / str(name)
    return HookState(name, path, _status(name, path))


def _write(path: Path, content: bytes) -> None:
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            os.fchmod(stream.fileno(), EXECUTABLE)
        Path(temporary).replace(path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            Path(temporary).unlink()
        raise


def install(repo: Repository, name: HookName) -> HookState:
    """Install or update a managed hook; a foreign hook is left as it is and fails."""
    state = inspect(repo, name)
    if state.status is HookStatus.CURRENT:
        return state
    if state.status is HookStatus.FOREIGN:
        message = (
            f"{state.path} exists and is not managed by quality; it was left unchanged. "
            f"Rename it to {name}.local to keep running it before the managed hook"
        )
        raise HookConflictError(CONFLICT.diagnostic(message))
    state.path.parent.mkdir(parents=True, exist_ok=True)
    _write(state.path, script(name).encode())
    return HookState(name, state.path, HookStatus.CURRENT)


def _status_finding(state: HookState, *, required: bool) -> Iterator[Diagnostic]:
    where = f"the {state.name} hook at {state.path}"
    match state.status:
        case HookStatus.CURRENT:
            return
        case HookStatus.FOREIGN if required:
            yield CONFLICT.diagnostic(f"{where} is not managed by quality")
        case HookStatus.MISSING | HookStatus.OUTDATED if required:
            yield MISSING.diagnostic(f"{where} is {state.status}")
        case HookStatus.OUTDATED:
            yield MISSING.diagnostic(f"{where} is outdated", severity=Severity.WARNING)
        case _:
            return


def status_diagnostics(
    states: Iterable[HookState], *, required: frozenset[HookName]
) -> list[Diagnostic]:
    """Return the doctor's findings: required hooks must be current; optional ones warn."""
    found: list[Diagnostic] = []
    for state in states:
        found.extend(_status_finding(state, required=state.name in required))
    return found
