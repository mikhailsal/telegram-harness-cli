"""The pinned Node and a component's locked npm packages (decision 0011).

Node is located only when a TypeScript component is prepared, so a repository
without one never needs it. Every snapshot work area installs each lockfile
directory once with `npm ci --offline` from the shared npm cache: install
scripts never run, and the user's and global npm configuration are replaced by
an empty file. Options on the command line take precedence over a project
`.npmrc`. A package missing from the cache is an environment error that names
the bootstrap command, which fills the cache with the same install online.

npm installs `node_modules` into the snapshot's lockfile directory, because the
tools import the component's configuration and resolve packages from it. The
inventory comes from the candidate's Git tree and the common gates have run
before any component is prepared, so the installation changes neither.

Tools run from the package Node would load for the component directory,
searched upward no further than the lockfile directory, and only at the pinned
version. Node's compile cache stays off: tools such as tsc, ESLint, and
Prettier would otherwise add the modules of every snapshot to one shared
directory in the system's temporary directory, which nothing ever removes.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from quality.adapters.native import fail, failure
from quality.adapters.settings import load_json_object, table
from quality.adapters.typescript.lockfile import NODE_MODULES
from quality.boundedio import ReportError
from quality.process import Command, ProcessError, run, sanitized

INSTALL_TIMEOUT = 900.0
# An online install downloads every locked package, which can take long on slow links.
DOWNLOAD_TIMEOUT = 3600.0
TOOL = "npm ci"
BOOTSTRAP_HINT = "run `./quality bootstrap` to download the locked packages"
NPM_CLI = ("lib", "node_modules", "npm", "bin", "npm-cli.js")
FLAGS = (
    "--ignore-scripts",
    "--include=dev",
    "--no-audit",
    "--no-fund",
    "--no-update-notifier",
    "--no-progress",
    "--loglevel=error",
)


@dataclass(frozen=True, slots=True)
class Node:
    """The pinned Node executable and the shared npm cache."""

    executable: Path
    cache: Path

    @property
    def npm(self) -> Path:
        """The npm command-line script bundled with this Node."""
        return self.executable.parent.parent.joinpath(*NPM_CLI)

    def environ(self, home: Path, extra: Mapping[str, str] | None = None) -> dict[str, str]:
        """Return a sanitized environment with a private home and this Node first on PATH.

        Node's compile cache is off, so no run leaves a cache behind.
        """
        home.mkdir(parents=True, exist_ok=True)
        settings = {"HOME": str(home), "NODE_DISABLE_COMPILE_CACHE": "1", **(extra or {})}
        return sanitized(path=[self.executable.parent], extra=settings)


def npm_ci(node: Node, directory: Path, home: Path, *, label: str, offline: bool = True) -> None:
    """Install the locked packages of `directory` from the cache, or download them."""
    home.mkdir(parents=True, exist_ok=True)
    # npm refuses to load one file as both the user and the global configuration.
    user, shared = home / "npmrc", home / "npmrc-global"
    for empty in (user, shared):
        empty.write_text("", encoding="utf-8")
    settings = (
        f"--cache={node.cache}",
        f"--userconfig={user}",
        f"--globalconfig={shared}",
        f"--logs-dir={home / 'logs'}",
    )
    flags = ("--offline",) if offline else ()
    argv = (str(node.executable), str(node.npm), "ci", *FLAGS, *settings, *flags)
    timeout = INSTALL_TIMEOUT if offline else DOWNLOAD_TIMEOUT
    command = Command(argv, cwd=directory, timeout=timeout, env=node.environ(home))
    try:
        completed = run(command)
    except ProcessError as error:
        raise failure(TOOL, f"{label}: {error}; {BOOTSTRAP_HINT}") from error
    if not completed.ok:
        detail = completed.stderr_text().strip() or f"exit status {completed.returncode}"
        fail(TOOL, f"{label}: {detail}; {BOOTSTRAP_HINT}")


@dataclass(slots=True)
class Environments:
    """Locates Node on first use and installs each lockfile directory once per work area."""

    locate: Callable[[], Path]
    cache: Path
    _node: Node | None = None
    _ready: set[tuple[Path, Path]] = field(default_factory=set[tuple[Path, Path]])

    def node(self) -> Node:
        """Return the pinned Node; raise ToolFailedError when it is not installed."""
        if self._node is None:
            self._node = Node(self.locate(), self.cache)
        return self._node

    def install(self, lock: Path, work: Path, *, label: str) -> Node:
        """Install a lockfile directory of the snapshot unless this work area already did."""
        node = self.node()
        if (work, lock) not in self._ready:
            home = work / "typescript-home" / f"npm-{len(self._ready)}"
            npm_ci(node, lock, home, label=label)
            self._ready.add((work, lock))
        return node


def package_directory(name: str, directory: Path, top: Path) -> Path | None:
    """Return where Node finds package `name` from `directory`, searching up to `top`."""
    for base in (path for path in (directory, *directory.parents) if path.is_relative_to(top)):
        candidate = base / NODE_MODULES / name
        if (candidate / "package.json").is_file():
            return candidate
    return None


def _bin(manifest: Mapping[str, object], name: str) -> str | None:
    value = manifest.get("bin")
    if isinstance(value, str):
        return value
    entries = table(manifest, "bin") or {}
    script = entries.get(PurePosixPath(name).name)
    return script if isinstance(script, str) else None


def tool_script(
    name: str, directory: Path, top: Path, version: str, command: str | None = None
) -> Path:
    """Return a command-line script of an installed package at the pinned version.

    `command` names the script among the package's `bin` entries; by default
    it is the package's name without its scope.
    """
    package = package_directory(name, directory, top)
    if package is None:
        fail(name, f"{name} is not installed for {directory}; {BOOTSTRAP_HINT}")
    try:
        manifest = load_json_object(package / "package.json")
    except ReportError as error:
        fail(name, str(error))
    if manifest.get("version") != version:
        fail(name, f"{package} holds version {manifest.get('version')!r}, not {version}")
    script = _bin(manifest, command or name)
    path = (package / script).resolve() if script is not None else None
    if path is None or not path.is_relative_to(package.resolve()) or not path.is_file():
        fail(name, f"{package} has no command-line script")
    return path
