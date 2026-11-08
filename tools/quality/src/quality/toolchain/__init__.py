"""Pinned standalone tools: manifest, cache location, and verified installation."""

from quality.toolchain.install import RULES as INSTALL_RULES
from quality.toolchain.install import (
    ToolchainError,
    cache_root,
    extra_path,
    install,
    locate,
    python_root,
)
from quality.toolchain.manifest import (
    Artifact,
    ManifestError,
    Tool,
    load_manifest,
    parse_manifest,
)
from quality.toolchain.update import RULES as UPDATE_RULES
from quality.toolchain.update import Updated, UpdateError, update

RULES = (*INSTALL_RULES, *UPDATE_RULES)

__all__ = [
    "RULES",
    "Artifact",
    "ManifestError",
    "Tool",
    "ToolchainError",
    "UpdateError",
    "Updated",
    "cache_root",
    "extra_path",
    "install",
    "load_manifest",
    "locate",
    "parse_manifest",
    "python_root",
    "update",
]
