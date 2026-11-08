"""The PHP helper: structure and imports from the pinned nikic/php-parser (decision 0013).

The helper is a PHP program in `tools/quality/helpers/php`, located relative
to this package so a materialized candidate core uses its own helper. It is
installed once into the cache, in a directory named by a digest of the
helper's composer.json, composer.lock, `bin/` and `src/` files and the PHP
version: a changed helper or PHP gets a new installation, and a stale one is
never used.

The installation works in a temporary copy of those files inside the cache
directory, where the pinned Composer installs the helper's runtime packages
from its own lockfile without the development ones, offline for a check and
online for bootstrap. The copy is renamed into place only when it is complete,
so a concurrent run never sees part of one, and an installation that lost its
program is replaced. Requests and responses follow the
protocol of the other helpers (`helperio`); the helper answers a request it
cannot serve with a `failure`.
"""

from __future__ import annotations

import hashlib
import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from quality.adapters import helperio
from quality.adapters.helperio import Entry, Fields
from quality.adapters.native import fail, failure, run_tool
from quality.adapters.php.composer import install, install_command
from quality.adapters.php.runtime import Php
from quality.boundedio import JsonValue
from quality.process import Command

# src/quality/adapters/php/helper.py -> tools/quality
CORE = Path(__file__).resolve().parents[4]
HELPER = CORE / "helpers" / "php"
MAIN = "bin/main.php"
MANIFESTS = ("composer.json", "composer.lock")
SOURCES = ("bin", "src")
TIMEOUT = 1800.0
TOOL = "php helper"
FIELDS = Fields(TOOL)
MEMORY = "memory_limit=2G"


def helper_files(source: Path) -> list[str]:
    """Return the helper's files that its installation is built from, relative to `source`."""
    files = [name for name in MANIFESTS if (source / name).is_file()]
    for directory in SOURCES:
        found = (path for path in (source / directory).rglob("*") if path.is_file())
        files += sorted(path.relative_to(source).as_posix() for path in found)
    return files


def digest(source: Path, version: str) -> str:
    """Return the digest of the helper's files and the PHP version."""
    hasher = hashlib.sha256(f"php-helper\0{version}\0".encode())
    for name in helper_files(source):
        data = (source / name).read_bytes()
        hasher.update(f"{name}\0{len(data)}\0".encode())
        hasher.update(data)
    return hasher.hexdigest()


@dataclass(slots=True)
class Helper:
    """The helper's sources and the cache directory of its installations."""

    source: Path
    cache: Path
    _programs: dict[Path, Path] = field(default_factory=dict[Path, Path])

    def _build(self, php: Php, target: Path, *, offline: bool) -> None:
        self.cache.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=self.cache, prefix="build-") as scratch:
            build, home = Path(scratch) / "helper", Path(scratch) / "home"
            for name in helper_files(self.source):
                copy = build / name
                copy.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(self.source / name, copy)
            command = install_command(php, build, home, online=not offline, development=False)
            install(command, TOOL)
            if not (build / "vendor" / "autoload.php").is_file():
                fail(TOOL, f"Composer installed no autoloader in {build}")
            if target.is_dir() and not (target / MAIN).is_file():
                # Only damage leaves an installation without its program; replace it.
                shutil.rmtree(target, ignore_errors=True)
            try:
                build.rename(target)
            except OSError as error:
                # A concurrent run that installed the same digest first is as good.
                if not (target / MAIN).is_file():
                    raise failure(
                        TOOL, f"cannot install the helper at {target}: {error}"
                    ) from error

    def program(self, php: Php, *, offline: bool = True) -> Path:
        """Return the installed helper for this PHP, installing it when the cache lacks it."""
        known = self._programs.get(php.executable)
        if known is not None and known.is_file():
            return known
        if not (self.source / MAIN).is_file():
            fail(TOOL, f"the helper is missing at {self.source / MAIN}")
        target = self.cache / digest(self.source, php.version)
        if not (target / MAIN).is_file():
            self._build(php, target, offline=offline)
        self._programs[php.executable] = target / MAIN
        return target / MAIN

    def run(
        self, php: Php, command: str, request: Mapping[str, JsonValue], *, work: Path, cwd: Path
    ) -> Entry:
        """Run one helper command and return its checked response."""
        program = self.program(php)
        handle = helperio.write_request(work / "php-requests", command, request)
        argv = php.command("-d", MEMORY, str(program), command, str(handle))
        env = php.environ(work / "php-home" / "helper")
        completed = run_tool(
            Command(argv, cwd=cwd, timeout=TIMEOUT, env=env, reap=True), name=TOOL, accepted={0}
        )
        return helperio.read_response(TOOL, command, completed)
