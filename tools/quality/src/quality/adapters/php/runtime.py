"""The system PHP, its Xdebug extension, and the pinned Composer (decision 0013).

PHP is a system program: the core does not install it, and the profile runs the
`php` of the standard system directories, never one an inherited PATH names. It
must be the minor version of `[runtimes.php]`, and Xdebug of `[runtimes.xdebug]`
must be available, either loaded by the system's configuration or loadable with
`-d zend_extension=xdebug`. Xdebug is the only driver the profile accepts: PCOV
and phpdbg measure lines but no branches, so a machine that has only them
cannot run the gate. The runtime is probed once per process, with Xdebug off.

Every PHP command runs in a sanitized environment, so `PHPRC` and
`PHP_INI_SCAN_DIR` are dropped and only the system's php.ini applies; the CLI
reads no php.ini from the working directory. Errors go to stderr, so stdout
holds only a tool's report, and Xdebug is off unless the command collects
coverage, when it is loaded if needed and set to coverage mode. Composer runs
from the pinned archive with a private home, the shared cache, no
interaction, no audit, a wide terminal so that it does not wrap its messages,
and the network disabled except while bootstrap downloads (decision 0010).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from quality.adapters.native import ToolFailedError, fail, run_tool
from quality.boundedio import ReportError, parse_json
from quality.process import Command, sanitized
from quality.process.environment import SYSTEM_PATH

PROBE_TIMEOUT = 60.0
TOOL = "php"
COLUMNS = "4000"
XDEBUG = ("-d", "zend_extension=xdebug")
PROBE = 'echo json_encode(["php" => PHP_VERSION, "xdebug" => phpversion("xdebug") ?: null]);'
PHP_HINT = "install PHP {php} with Xdebug {xdebug} (decision 0002)"
COMPOSER_HINT = "run `./quality bootstrap` to install the pinned Composer"


@dataclass(frozen=True, slots=True)
class Requirements:
    """The minor versions of PHP and Xdebug that the profile requires."""

    php: str
    xdebug: str

    @property
    def hint(self) -> str:
        """Say what to install."""
        return PHP_HINT.format(php=self.php, xdebug=self.xdebug)


@dataclass(frozen=True, slots=True)
class Php:
    """A probed PHP with Xdebug, the pinned Composer archive, and the shared Composer cache."""

    executable: Path
    version: str
    xdebug_loaded: bool
    composer: Path
    cache: Path

    def command(self, *arguments: str, coverage: bool = False) -> tuple[str, ...]:
        """Return a PHP command; with `coverage`, Xdebug is loaded unless it already is."""
        load = XDEBUG if coverage and not self.xdebug_loaded else ()
        return (str(self.executable), "-d", "display_errors=stderr", *load, *arguments)

    def composer_command(self, *arguments: str) -> tuple[str, ...]:
        """Return a command of the pinned Composer."""
        return self.command(str(self.composer), *arguments)

    def environ(
        self,
        home: Path,
        extra: Mapping[str, str] | None = None,
        *,
        coverage: bool = False,
        online: bool = False,
    ) -> dict[str, str]:
        """Return a sanitized environment with a private home and Composer's settings."""
        home.mkdir(parents=True, exist_ok=True)
        settings = {
            "HOME": str(home),
            "COMPOSER_HOME": str(home / "composer"),
            "COMPOSER_CACHE_DIR": str(self.cache),
            "COMPOSER_NO_INTERACTION": "1",
            "COMPOSER_NO_AUDIT": "1",
            "COLUMNS": COLUMNS,
            "XDEBUG_MODE": "coverage" if coverage else "off",
            **({} if online else {"COMPOSER_DISABLE_NETWORK": "1"}),
            **(extra or {}),
        }
        return sanitized(path=[self.executable.parent], extra=settings)

    def run_in(
        self,
        argv: tuple[str, ...],
        directory: Path,
        home: Path,
        *,
        timeout: float,
        online: bool = False,
    ) -> Command:
        """Build a reaped command that runs in `directory` with a private home."""
        env = self.environ(home, online=online)
        return Command(argv, cwd=directory, timeout=timeout, env=env, reap=True)


def system_php() -> Path:
    """Return the `php` of the first system directory that has one."""
    candidates = (Path(directory) / "php" for directory in SYSTEM_PATH)
    found = next((candidate for candidate in candidates if candidate.is_file()), None)
    if found is None:
        fail(TOOL, f"PHP is not installed in {', '.join(SYSTEM_PATH)}")
    return found


def _probe(executable: Path, *flags: str) -> tuple[str, str | None]:
    """Return the PHP version and the version of Xdebug, or None when it is not loaded."""
    argv = (str(executable), "-d", "display_errors=stderr", *flags, "-r", PROBE)
    env = sanitized(path=[executable.parent], extra={"XDEBUG_MODE": "off"})
    command = Command(argv, cwd=executable.parent, timeout=PROBE_TIMEOUT, env=env)
    completed = run_tool(command, name=TOOL, accepted={0})
    try:
        answer = parse_json(completed.stdout, f"{executable} probe")
    except ReportError as error:
        fail(TOOL, str(error))
    version = answer.get("php") if isinstance(answer, dict) else None
    xdebug = answer.get("xdebug") if isinstance(answer, dict) else None
    if not isinstance(version, str) or not (xdebug is None or isinstance(xdebug, str)):
        fail(TOOL, f"{executable} answered the probe with {answer!r}")
    return version, xdebug


def _minor(version: str) -> str:
    return ".".join(version.split(".")[:2])


def probed(executable: Path, composer: Path, cache: Path, required: Requirements) -> Php:
    """Probe a PHP and require the pinned PHP and Xdebug minor versions."""
    version, xdebug = _probe(executable)
    loaded = xdebug is not None
    if xdebug is None:
        xdebug = _probe(executable, *XDEBUG)[1]
    if _minor(version) != required.php:
        fail(
            TOOL,
            f"{executable} is PHP {version}; the profile requires {required.php}; {required.hint}",
        )
    if xdebug is None:
        fail(
            TOOL,
            f"Xdebug is not available to {executable}; branch coverage needs it, which PCOV "
            f"and phpdbg cannot replace; {required.hint}",
        )
    if _minor(xdebug) != required.xdebug:
        fail(
            TOOL,
            f"{executable} has Xdebug {xdebug}; the profile requires {required.xdebug}; "
            f"{required.hint}",
        )
    return Php(executable, version, loaded, composer, cache)


def located(locate: Callable[[], Path]) -> Path:
    """Locate Composer; a missing one names the command that installs it."""
    try:
        return locate()
    except ToolFailedError as error:
        message = f"{error.diagnostic.message}; {COMPOSER_HINT}"
        raise ToolFailedError(dataclasses.replace(error.diagnostic, message=message)) from error


@dataclass(slots=True)
class Runtime:
    """Locates and probes PHP and Composer on first use."""

    locate_php: Callable[[], Path]
    locate_composer: Callable[[], Path]
    cache: Path
    required: Requirements
    _php: Php | None = None

    def php(self) -> Php:
        """Return the probed PHP; raise ToolFailedError when it does not meet the pins."""
        if self._php is None:
            composer = located(self.locate_composer)
            self._php = probed(self.locate_php(), composer, self.cache, self.required)
        return self._php
