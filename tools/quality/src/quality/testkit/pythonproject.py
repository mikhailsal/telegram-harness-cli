"""A scratch Python component that syncs offline: it locks only what the core locks.

The lockfile copies the core's own entries for coverage, pytest, and pytest-cov and
their dependencies, so the packages a bootstrapped core installed are all it needs.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from quality.adapters.python.config import TEST_PACKAGES

CORE = Path(__file__).resolve().parents[3]
CORE_LOCK = CORE / "uv.lock"
CORE_PYTHON = CORE / ".python-version"
SEPARATOR = "\n[[package]]\n"

PYPROJECT = """\
[project]
name = "{name}"
version = "0.1.0"
requires-python = "=={series}.*"
dependencies = []

[dependency-groups]
dev = [{pins}]

[tool.uv]
package = false

[tool.ruff]
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I"]

[tool.pyright]
typeCheckingMode = "strict"

[tool.pytest.ini_options]
pythonpath = ["src"]
"""

ROOT = """\
name = "{name}"
version = "0.1.0"
source = {{ virtual = "." }}

[package.dev-dependencies]
dev = [
{names}]

[package.metadata]

[package.metadata.requires-dev]
dev = [
{specifiers}]
"""


def _blocks(lock: str) -> tuple[str, dict[str, str]]:
    header, *blocks = lock.split(SEPARATOR)
    return header, {str(tomllib.loads(block)["name"]): block for block in blocks}


def _closure(blocks: Mapping[str, str]) -> list[str]:
    pending: list[str] = [*TEST_PACKAGES]
    found: set[str] = set()
    while pending:
        name = pending.pop()
        if name not in found:
            found.add(name)
            listed = tomllib.loads(blocks[name]).get("dependencies", [])
            pending.extend(str(entry["name"]) for entry in cast(list[dict[str, object]], listed))
    return sorted(found)


def _versions(blocks: Mapping[str, str]) -> dict[str, str]:
    return {name: str(tomllib.loads(blocks[name])["version"]) for name in TEST_PACKAGES}


def lockfile(name: str, core_lock: Path = CORE_LOCK) -> str:
    """Return a uv.lock for a virtual project with the core's test packages as its dev group."""
    header, blocks = _blocks(core_lock.read_text(encoding="utf-8"))
    versions = _versions(blocks)
    root = ROOT.format(
        name=name,
        names="".join(f'    {{ name = "{package}" }},\n' for package in TEST_PACKAGES),
        specifiers="".join(
            f'    {{ name = "{package}", specifier = "=={version}" }},\n'
            for package, version in versions.items()
        ),
    )
    packages = {package: blocks[package] for package in _closure(blocks)} | {name: root}
    return header + "".join(SEPARATOR + packages[key] for key in sorted(packages))


def pyproject(name: str, version: str, core_lock: Path = CORE_LOCK) -> str:
    """Return a pyproject.toml that the Python profile accepts."""
    versions = _versions(_blocks(core_lock.read_text(encoding="utf-8"))[1])
    pins = ", ".join(f'"{package}=={pinned}"' for package, pinned in versions.items())
    series = ".".join(version.split(".")[:2])
    return PYPROJECT.format(name=name, series=series, pins=pins)


def python_project(directory: Path, files: Mapping[str, str], *, name: str = "app") -> Path:
    """Write a complete component with `files` into `directory` and return the directory."""
    version = CORE_PYTHON.read_text(encoding="utf-8").strip()
    written = {
        "pyproject.toml": pyproject(name, version),
        "uv.lock": lockfile(name),
        ".python-version": f"{version}\n",
        **files,
    }
    for path, text in written.items():
        target = directory / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return directory
