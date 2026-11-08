"""Composer: validating a component's manifest and installing its locked packages.

Every snapshot work area prepares each PHP component once. `composer validate
--check-lock` first checks composer.json against Composer's schema and the
lockfile against composer.json: a schema error or a lockfile that composer.json
has outgrown is a `php.config` finding at the file Composer names, and nothing
is installed. `composer install --no-scripts --no-plugins` then installs the
locked packages from the shared cache into the snapshot's `vendor/` without
running any script or plugin of the component. The inventory comes from the
candidate's Git tree and the common gates have run before any component is
prepared, so the installation changes neither. A package missing from the cache
is an environment error that names the bootstrap command, which fills the
cache with the same installation online.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from quality.adapters.native import fail, failure, run_tool
from quality.adapters.php.lockfile import LOCKFILE, MANIFEST
from quality.adapters.php.runtime import Php, Runtime
from quality.adapters.settings import Report
from quality.process import Command, ProcessError, run

VALIDATE_TIMEOUT = 300.0
INSTALL_TIMEOUT = 900.0
# An online installation downloads every locked package, which can take long on slow links.
DOWNLOAD_TIMEOUT = 3600.0
VALIDATE = ("validate", "--no-check-publish", "--no-check-all", "--check-lock")
INSTALL = ("install", "--no-progress", "--no-scripts", "--no-plugins")
INSTALL_TOOL = "composer install"
BOOTSTRAP_HINT = "run `./quality bootstrap` to download the locked packages"
# `composer validate` exits 1 when composer.json cannot be loaded and 2 when it lists errors.
UNLOADABLE, ERRORS = 1, 2
SECTIONS = {"# General errors": MANIFEST, "# Lock file errors": LOCKFILE}
EXCEPTION = re.compile(r"^In \S+ line \d+:$")


def _listed(output: str) -> list[tuple[str, str]]:
    """Return the errors of the sections that `composer validate` prints, by file."""
    found: list[tuple[str, str]] = []
    current: str | None = None
    for line in output.splitlines():
        if line.startswith("# "):
            current = SECTIONS.get(line.strip())
        elif current is not None and line.startswith("- "):
            found.append((current, line[2:].strip()))
    return found


def _exception(output: str) -> str:
    """Return the text of the exception Composer printed, without its source and usage lines."""
    lines = (line.strip() for line in output.splitlines())
    kept = (line for line in lines if line and not EXCEPTION.match(line))
    return " ".join(line for line in kept if not line.startswith("validate ["))


def validate(php: Php, directory: Path, home: Path) -> list[tuple[str, str]]:
    """Return the problems Composer finds in composer.json and composer.lock, by file."""
    argv = php.composer_command(*VALIDATE)
    completed = run_tool(
        php.run_in(argv, directory, home, timeout=VALIDATE_TIMEOUT),
        name="composer validate",
        accepted={0, UNLOADABLE, ERRORS},
    )
    output = completed.stderr_text()
    if completed.returncode == UNLOADABLE:
        return [(MANIFEST, _exception(output) or "composer.json cannot be loaded")]
    found = _listed(output) if completed.returncode == ERRORS else []
    if completed.returncode == ERRORS and not found:
        fail("composer validate", f"exit status {ERRORS} without errors: {output}")
    return found


def install_command(
    php: Php, directory: Path, home: Path, *, online: bool = False, development: bool = True
) -> Command:
    """Return the command that installs the locked packages of `directory`."""
    argv = php.composer_command(*INSTALL, *(() if development else ("--no-dev",)))
    timeout = DOWNLOAD_TIMEOUT if online else INSTALL_TIMEOUT
    return php.run_in(argv, directory, home, timeout=timeout, online=online)


def install(command: Command, label: str) -> None:
    """Install the locked packages from the cache, or download them when online."""
    try:
        completed = run(command)
    except ProcessError as error:
        raise failure(INSTALL_TOOL, f"{label}: {error}; {BOOTSTRAP_HINT}") from error
    if not completed.ok:
        output = completed.stderr_text().strip() or completed.stdout_text().strip()
        detail = output or f"exit status {completed.returncode}"
        fail(INSTALL_TOOL, f"{label}: {detail}; {BOOTSTRAP_HINT}")


@dataclass(slots=True)
class Environments:
    """Probes PHP on first use and installs each component once per work area."""

    runtime: Runtime
    _ready: set[tuple[Path, Path]] = field(default_factory=set[tuple[Path, Path]])

    def install(self, directory: Path, work: Path, *, label: str, add: Report) -> Php:
        """Validate and install a component of the snapshot unless this work area already did.

        Composer's findings are reported through `add`, and nothing is installed then.
        """
        php = self.runtime.php()
        if (work, directory) in self._ready:
            return php
        home = work / "php-home" / label
        problems = validate(php, directory, home)
        for file, message in problems:
            add(file, f"composer validate: {message}")
        if not problems:
            install(install_command(php, directory, home), label)
            self._ready.add((work, directory))
        return php
