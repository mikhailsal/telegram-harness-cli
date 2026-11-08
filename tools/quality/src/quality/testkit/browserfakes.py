"""Fakes for browser component tests: locked browser packages, Playwright, and Chromium.

A browser component's lockfile locks the Node pins and the browser pins; the
fake `npm ci` can install a playwright-core whose `browsers.json` names a
headless shell version. The fake Chromium is a shell script that prints its
version like the real one, and the fake `ldd` prints canned loader lines, so
unit tests need neither the browser nor its libraries.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from quality.adapters.typescript.chromium import Chromium
from quality.testkit.nodefakes import PINS, lockfile, script, write_component

BROWSER_PINS = {
    "react": "19.3.1",
    "react-dom": "19.3.1",
    "@types/react": "19.3.0",
    "@types/react-dom": "19.3.0",
    "vite": "8.3.5",
    "@vitejs/plugin-react": "6.1.2",
    "jsdom": "30.1.3",
    "@testing-library/react": "16.3.3",
    "@testing-library/dom": "10.4.2",
    "@testing-library/user-event": "14.6.8",
    "eslint-plugin-react-hooks": "7.1.1",
    "eslint-plugin-jsx-a11y-x": "0.2.0",
    "globals": "17.13.0",
    "stylelint": "17.16.0",
    "stylelint-config-standard": "40.0.0",
    "stylelint-config-css-modules": "4.6.0",
    "@playwright/test": "1.64.1",
    "@axe-core/playwright": "4.14.0",
}
ALL_PINS = {**PINS, **BROWSER_PINS}
CHROMIUM = "156.0.8078.4"
# What `@playwright/test` loads: its `playwright`, which loads `playwright-core`.
PLAYWRIGHT = {
    f"node_modules/{name}": {"version": "1.64.1"} for name in ("playwright", "playwright-core")
}
LIBRARIES = "\tlibc.so.6 => /lib/libc.so.6 (0x1)\n\t/lib64/ld-linux-x86-64.so.2 (0x2)\n"


def write_browser_component(directory: Path, pins: dict[str, str] = ALL_PINS) -> Path:
    """Write a TypeScript component whose lockfile also locks Playwright and `pins`."""
    write_component(directory, pins)
    (directory / "package-lock.json").write_text(lockfile(pins, PLAYWRIGHT))
    return directory


def browsers_json(version: str = CHROMIUM) -> str:
    """Return the `browsers.json` of a playwright-core made for this headless shell."""
    shell = {"name": "chromium-headless-shell", "revision": "1248", "browserVersion": version}
    return json.dumps({"browsers": [{"name": "chromium", "browserVersion": version}, shell]})


def install_playwright(version: str = CHROMIUM, where: str = "node_modules/playwright-core") -> str:
    """Return an `npm ci` body that installs a playwright-core made for `version`."""
    return f"mkdir -p '{where}'\nprintf '%s' '{browsers_json(version)}' > '{where}/browsers.json'\n"


@dataclass(frozen=True, slots=True)
class FakeChromium:
    """A headless shell script that prints a version, and an `ldd` script."""

    executable: Path
    ldd: Path

    @classmethod
    def create(cls, root: Path, version: str = CHROMIUM) -> FakeChromium:
        """Install the fake browser and `ldd` under `root`."""
        executable = script(
            root / "chromium" / "chrome-headless-shell",
            f"echo 'Google Chrome for Testing {version}'\n",
        )
        ldd = script(root / "bin" / "ldd", f"printf '{LIBRARIES}'\n")
        return cls(executable, ldd)

    def chromium(self, calls: list[str], *, ldd: bool = True) -> Chromium:
        """Return a Chromium setup that records whether it located or installed the browser."""

        def locate() -> Path:
            calls.append("locate chromium")
            return self.executable

        def install() -> Path:
            calls.append("install chromium")
            return self.executable

        return Chromium(locate, install, self.ldd if ldd else None)
