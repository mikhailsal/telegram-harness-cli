"""The pinned Dart SDK and a component's locked pub packages (decision 0012).

The SDK is located only when a Dart component is prepared, so a repository
without one never needs it. Every Dart command runs with the SDK's `bin` first
on PATH, the shared pub cache, a private home in the work area, and analytics
off: `DART_SUPPRESS_ANALYTICS` always, and `--suppress-analytics` for the `dart`
tool's own commands. The VM rejects that option when it runs a compiled
program, so the helper runs without it. Every command is reaped: whatever it
leaves in its process group, such as an analysis server or a VM service, is
killed when it exits.

Every snapshot work area resolves each lock directory once with
`dart pub get --offline --enforce-lockfile`. pub writes `.dart_tool/` into the
snapshot's lock directory and may not change the lockfile. The inventory comes
from the candidate's Git tree and the common gates have run before any
component is prepared, so the resolution changes neither. A package missing
from the cache is an environment error that names the bootstrap command, which
fills the cache with the same resolution online.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from quality.adapters.native import ToolFailedError, fail, failure
from quality.process import Command, ProcessError, run, sanitized

GET_TIMEOUT = 900.0
# An online resolution downloads every locked package, which can take long on slow links.
DOWNLOAD_TIMEOUT = 3600.0
TOOL = "dart pub get"
BOOTSTRAP_HINT = "run `./quality bootstrap` to download the locked packages"
SDK_HINT = "run `./quality bootstrap` to install the pinned Dart SDK"


@dataclass(frozen=True, slots=True)
class Sdk:
    """The pinned `dart` executable and the shared pub cache."""

    executable: Path
    cache: Path

    @property
    def version_file(self) -> Path:
        """The file in which the SDK records its version."""
        return self.executable.parent.parent / "version"

    def command(self, *arguments: str) -> tuple[str, ...]:
        """Return a command of the `dart` tool with analytics off."""
        return (str(self.executable), "--suppress-analytics", *arguments)

    def environ(self, home: Path, extra: Mapping[str, str] | None = None) -> dict[str, str]:
        """Return a sanitized environment with a private home and this SDK first on PATH."""
        home.mkdir(parents=True, exist_ok=True)
        settings = {
            "HOME": str(home),
            "PUB_CACHE": str(self.cache),
            "DART_SUPPRESS_ANALYTICS": "true",
            **(extra or {}),
        }
        return sanitized(path=[self.executable.parent], extra=settings)

    def run_in(
        self, argv: tuple[str, ...], directory: Path, home: Path, *, timeout: float
    ) -> Command:
        """Build a reaped command that runs in `directory` with a private home."""
        return Command(argv, cwd=directory, timeout=timeout, env=self.environ(home), reap=True)


def pub_get(sdk: Sdk, directory: Path, home: Path, *, label: str, offline: bool = True) -> None:
    """Resolve the lockfile of `directory` from the cache, or download its packages."""
    flags = ("--offline",) if offline else ()
    timeout = GET_TIMEOUT if offline else DOWNLOAD_TIMEOUT
    argv = sdk.command("pub", "get", "--enforce-lockfile", *flags)
    try:
        completed = run(sdk.run_in(argv, directory, home, timeout=timeout))
    except ProcessError as error:
        raise failure(TOOL, f"{label}: {error}; {BOOTSTRAP_HINT}") from error
    if not completed.ok:
        output = completed.stderr_text().strip() or completed.stdout_text().strip()
        fail(TOOL, f"{label}: {output or f'exit status {completed.returncode}'}; {BOOTSTRAP_HINT}")


def located(locate: Callable[[], Path]) -> Path:
    """Locate the SDK; a missing one names the command that installs it."""
    try:
        return locate()
    except ToolFailedError as error:
        message = f"{error.diagnostic.message}; {SDK_HINT}"
        raise ToolFailedError(dataclasses.replace(error.diagnostic, message=message)) from error


@dataclass(slots=True)
class Environments:
    """Locates the SDK on first use and resolves each lock directory once per work area."""

    locate: Callable[[], Path]
    cache: Path
    _sdk: Sdk | None = None
    _ready: set[tuple[Path, Path]] = field(default_factory=set[tuple[Path, Path]])

    def sdk(self) -> Sdk:
        """Return the pinned SDK; raise ToolFailedError when it is not installed."""
        if self._sdk is None:
            self._sdk = Sdk(located(self.locate), self.cache)
        return self._sdk

    def install(self, lock: Path, work: Path, *, label: str) -> Sdk:
        """Resolve a lock directory of the snapshot unless this work area already did."""
        sdk = self.sdk()
        if (work, lock) not in self._ready:
            home = work / "dart-home" / f"pub-{len(self._ready)}"
            pub_get(sdk, lock, home, label=label)
            self._ready.add((work, lock))
        return sdk
