"""The pinned Chromium of browser components: installed, started, and matched to Playwright.

Bootstrap installs the Chrome for Testing headless shell of `toolchains.toml`
into the quality cache, never into Playwright's own cache, and doctor locates
it without downloading (decision 0017). Both then prove that it starts on this
machine: `ldd` names the Linux libraries the dynamic loader cannot find, and
`--version` reports the version that ran. The `browsers.json` of the
playwright-core that the component's locked `@playwright/test` loads must
expect that version, or its scenarios would drive a browser that Playwright
release was not made for.
"""

from __future__ import annotations

import re
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from quality.adapters.native import ToolFailedError
from quality.adapters.settings import load_json_object
from quality.adapters.typescript.lockfile import Lock
from quality.boundedio import ReportError
from quality.diagnostics import Diagnostic, Kind, Rule, Severity
from quality.process import Command, ProcessError, run, sanitized
from quality.process.environment import SYSTEM_PATH

LIBRARIES = Rule(
    id="typescript.browser-libraries",
    title="The pinned Chromium cannot start on this machine",
    requirement="R02",
    explanation=(
        "Browser scenarios run in the pinned Chrome for Testing headless shell, which links "
        "system libraries such as NSS, GLib, and ALSA. Without them it cannot start, so no "
        "browser gate can run."
    ),
    remedy=(
        "Install the system packages that provide the named libraries; `deb.deps` and "
        "`rpm.deps` beside the browser list them for Debian and RPM distributions."
    ),
    kind=Kind.ENVIRONMENT,
)
VERSION = Rule(
    id="typescript.browser-version",
    title="The locked Playwright expects another Chromium",
    requirement="R02",
    explanation=(
        "Each Playwright release names the one Chromium build it was made for in the "
        "`browsers.json` of its playwright-core package. The gate runs only the pinned "
        "headless shell, so a lockfile that installs another playwright-core would run the "
        "component's scenarios in a browser that its Playwright does not expect."
    ),
    remedy="Lock @playwright/test at the version toolchains.toml pins, without overrides.",
    kind=Kind.ENVIRONMENT,
)
READY = Rule(
    id="typescript.browser-ready",
    title="The pinned Chromium starts and matches the locked Playwright",
    requirement="R02",
    explanation="Bootstrap and doctor name the browser that a component's scenarios run in.",
    remedy="Nothing to do.",
    severity=Severity.INFO,
)
RULES = (LIBRARIES, VERSION, READY)

TOOL = "chromium"
# The packages through which a component's Playwright Test loads its playwright-core.
PLAYWRIGHT = ("@playwright/test", "playwright", "playwright-core")
BROWSERS = "browsers.json"
SHELL = "chromium-headless-shell"
TIMEOUT = 60.0
NOT_FOUND = re.compile(r"^\s*(\S+) => not found\s*$", re.MULTILINE)


def expected_version(lock: Lock, directory: Path, installed: Path) -> str:
    """Return the headless shell version that the locked Playwright expects.

    `directory` is the component's directory below the lockfile's, and
    `installed` the directory where that lockfile's packages were installed.
    Raise ReportError when the lockfile or the installed package does not say.
    """
    where, key = directory, ""
    for name in PLAYWRIGHT:
        found = lock.resolve(name, where)
        if found is None:
            msg = f"the lockfile installs no {name} for the component's Playwright Test"
            raise ReportError(msg)
        key = found[0]
        where = lock.directory / key
    listed = load_json_object(installed / key / BROWSERS).get("browsers")
    entries = cast(list[object], listed) if isinstance(listed, list) else []
    for entry in entries:
        if isinstance(entry, Mapping) and cast(Mapping[str, object], entry).get("name") == SHELL:
            version = cast(Mapping[str, object], entry).get("browserVersion")
            if isinstance(version, str):
                return version
    msg = f"{key}/{BROWSERS} names no {SHELL} version"
    raise ReportError(msg)


def system_ldd() -> Path | None:
    """Return the `ldd` of the first system directory that has one, if any."""
    candidates = (Path(directory) / "ldd" for directory in SYSTEM_PATH)
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def _run(argv: tuple[str, ...], cwd: Path, home: Path) -> tuple[int, str, str]:
    command = Command(argv, cwd=cwd, timeout=TIMEOUT, env=sanitized(extra={"HOME": str(home)}))
    try:
        completed = run(command)
    except ProcessError as error:
        return -1, "", str(error)
    return completed.returncode, completed.stdout_text(), completed.stderr_text().strip()


@dataclass(frozen=True, slots=True)
class Chromium:
    """Installs or locates the pinned headless shell and proves that it starts here."""

    locate: Callable[[], Path]
    install: Callable[[], Path]
    ldd: Path | None = field(default_factory=system_ldd)

    def ready(self, component: str, expected: str, *, offline: bool) -> list[Diagnostic]:
        """Install the browser, or only locate it `offline`, and check it against `expected`."""
        try:
            executable = self.locate() if offline else self.install()
        except ToolFailedError as error:
            return [error.diagnostic]
        return [self._checked(executable, component, expected)]

    def executable(self, component: str, expected: str) -> Path:
        """Locate the browser a run drives; raise ToolFailedError unless it is ready."""
        executable = self.locate()
        finding = self._checked(executable, component, expected)
        if finding.rule != READY.id:
            raise ToolFailedError(finding)
        return executable

    def _checked(self, executable: Path, component: str, expected: str) -> Diagnostic:
        with tempfile.TemporaryDirectory(prefix="quality-chromium-") as scratch:
            home = Path(scratch)
            missing = self._missing(executable, home)
            if missing:
                found = ", ".join(missing)
                message = f"{executable} cannot start: the libraries {found} are not found"
                return LIBRARIES.diagnostic(message, component=component)
            status, out, err = _run((str(executable), "--version"), home, home)
        if status != 0:
            detail = err or f"exit status {status}"
            message = f"{executable} does not start: {detail}"
            return LIBRARIES.diagnostic(message, component=component)
        version = (out.split() or [""])[-1]
        if version != expected:
            message = (
                f"the locked Playwright expects Chromium {expected}, "
                f"but the pinned browser is {version!r}"
            )
            return VERSION.diagnostic(message, component=component)
        message = f"Chromium {version} starts and matches the locked Playwright: {executable}"
        return READY.diagnostic(message, component=component)

    def _missing(self, executable: Path, home: Path) -> list[str]:
        """Name the libraries the dynamic loader cannot find; none when `ldd` cannot tell."""
        if self.ldd is None:
            return []
        status, out, _ = _run((str(self.ldd), str(executable)), executable.parent, home)
        return NOT_FOUND.findall(out) if status == 0 else []
