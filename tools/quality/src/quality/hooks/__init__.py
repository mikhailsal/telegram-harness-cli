"""Managed Git hooks: check-only entry points that call the `quality` launcher."""

from quality.hooks.install import (
    CONFLICT,
    MISSING,
    RULES,
    HookConflictError,
    HookState,
    HookStatus,
    hooks_directory,
    inspect,
    install,
    status_diagnostics,
)
from quality.hooks.scripts import HookName, script

__all__ = [
    "CONFLICT",
    "MISSING",
    "RULES",
    "HookConflictError",
    "HookName",
    "HookState",
    "HookStatus",
    "hooks_directory",
    "inspect",
    "install",
    "script",
    "status_diagnostics",
]
