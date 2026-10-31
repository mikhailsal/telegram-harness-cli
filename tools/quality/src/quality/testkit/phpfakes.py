"""Fakes for PHP adapter tests: a `php` whose scripts are shell scripts, and components.

The fake `php` logs its working directory and arguments, skips the `-d`
settings, and hands the rest to `handlers/<name>.sh`, where `<name>` is the
base name of the script it runs, such as `composer`, `phpstan`, or `main.php`,
or `probe` for `-r`; a call without a handler succeeds silently. A handler sees
`FAKE_XDEBUG=1` when the call loads Xdebug. The default probe answers PHP
8.4.14 with Xdebug 3.5.3, loaded by the system unless `loaded` is false. Unit
tests thus run without PHP or the Composer cache.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from quality.adapters.php.runtime import Php, Requirements
from quality.policy import Component
from quality.testing import Suite, SuiteRequest
from quality.testkit.nodefakes import script
from quality.testkit.policies import ComponentSpec, build_policy

PINS = {
    "phpunit/phpunit": "13.4.3",
    "phpunit/php-code-coverage": "14.4.1",
    "phpstan/phpstan": "2.3.1",
    "friendsofphp/php-cs-fixer": "3.95.27",
}
REQUIRED = Requirements("8.4", "3.5")
VERSION = "8.4.14"
XDEBUG = "3.5.3"
MAIN = """\
log='{log}'
for arg in "$PWD" "$@"; do printf '%s\\t' "$arg" >> "$log"; done
echo >> "$log"
xdebug=
while [ "$1" = -d ]; do
  [ "$2" = zend_extension=xdebug ] && xdebug=1
  shift 2
done
export FAKE_XDEBUG="$xdebug"
case "$1" in -r) name=probe ;; *) name="${{1##*/}}" ;; esac
handler='{handlers}'/"$name.sh"
[ -f "$handler" ] || exit 0
exec /bin/sh "$handler" "$@"
"""


def probe(version: str = VERSION, xdebug: str | None = XDEBUG, *, loaded: bool = True) -> str:
    """Return a probe handler: Xdebug answers only when loaded, by the system or the call."""
    answer = json.dumps({"php": version, "xdebug": xdebug})
    unloaded = json.dumps({"php": version, "xdebug": None})
    if loaded:
        return f"printf '%s' '{answer}'\n"
    return (
        f"""if [ -n "$FAKE_XDEBUG" ]; then printf '%s' '{answer}'; """
        f"""else printf '%s' '{unloaded}'; fi\n"""
    )


def php_component(
    root: str = "app", source: tuple[str, ...] = ("src",), integration: tuple[str, ...] = ()
) -> Component:
    """Return a PHP component `app` rooted at `root` with unit tests in `tests`."""
    spec = ComponentSpec(
        id="app",
        root=root,
        profile="php",
        source=source,
        unit=("tests",),
        integration=integration,
        capabilities=(),
    )
    (found,) = build_policy(spec).components
    return found


def suite_request(
    directory: Path,
    suite: Suite = Suite.UNIT,
    component: Component | None = None,
    roots: tuple[str, ...] = ("tests",),
) -> SuiteRequest:
    """Return a request to run one suite of `component` in a snapshot below `directory`."""
    work = directory / "work"
    output = work / f"app-{suite}"
    output.mkdir(parents=True)
    (directory / "snapshot" / "app").mkdir(parents=True, exist_ok=True)
    component = component or php_component()
    return SuiteRequest(component, suite, roots, directory / "snapshot", output, 0.0, work)


def package(
    name: str, version: str, url: str | None = None, kind: str = "zip"
) -> dict[str, object]:
    """Return a lockfile entry that installs `name` from an archive."""
    archive = url or f"https://api.github.com/repos/{name}/zipball/{version}"
    return {"name": name, "version": version, "dist": {"type": kind, "url": archive}}


def composer_lock(
    pins: Mapping[str, str] = PINS, extra: Sequence[Mapping[str, object]] = ()
) -> str:
    """Render a lockfile that locks `pins` as development packages, plus `extra` packages."""
    document = {
        "content-hash": "0" * 32,
        "packages": [dict(entry) for entry in extra],
        "packages-dev": [package(name, version) for name, version in pins.items()],
    }
    return json.dumps(document, indent=4) + "\n"


def composer_json(**fields: object) -> str:
    """Render a composer.json with the `App` prefix in `src/` and the pins as development ones."""
    document: dict[str, object] = {
        "require-dev": dict(PINS),
        "autoload": {"psr-4": {"App\\": "src/"}},
        "autoload-dev": {"psr-4": {"App\\Tests\\": "tests/"}},
    }
    document.update(fields)
    return json.dumps(document, indent=4) + "\n"


def write_component(directory: Path, pins: Mapping[str, str] = PINS) -> Path:
    """Write a valid PHP component into `directory` and return it."""
    (directory / "src").mkdir(parents=True, exist_ok=True)
    (directory / "tests").mkdir(exist_ok=True)
    (directory / "composer.json").write_text(composer_json())
    (directory / "composer.lock").write_text(composer_lock(pins))
    return directory


def write_helper(directory: Path) -> Path:
    """Write the files a PHP helper is installed from into `directory` and return it."""
    (directory / "bin").mkdir(parents=True, exist_ok=True)
    (directory / "src").mkdir(exist_ok=True)
    (directory / "composer.json").write_text("{}\n")
    (directory / "composer.lock").write_text('{"packages": []}\n')
    (directory / "bin" / "main.php").write_text("<?php\n")
    (directory / "src" / "Cli.php").write_text("<?php\n")
    return directory


@dataclass(frozen=True, slots=True)
class FakePhp:
    """A fake PHP; each `php` call is logged and dispatched to a replaceable handler."""

    php: Php
    root: Path

    @classmethod
    def create(cls, root: Path, cache: Path, *, loaded: bool = True) -> FakePhp:
        """Install the fake PHP under `root`, using `cache` as the Composer cache."""
        log, handlers = root / "php.log", root / "handlers"
        executable = script(root / "bin" / "php", MAIN.format(log=log, handlers=handlers))
        composer = root / "composer" / "composer"
        composer.parent.mkdir(parents=True)
        composer.write_text("<?php // the fake Composer archive\n")
        fake = cls(Php(executable, VERSION, loaded, composer, cache), root)
        fake.handle("probe", probe(loaded=loaded))
        return fake

    def handle(self, name: str, body: str) -> None:
        """Replace what the fake does for the script `name` after logging its call."""
        script(self.root / "handlers" / f"{name}.sh", body)

    def calls(self) -> list[list[str]]:
        """Return each `php` call: its working directory, then its arguments."""
        log = self.root / "php.log"
        lines = log.read_text().splitlines() if log.exists() else []
        return [line.split("\t")[:-1] for line in lines]

    def scripts(self) -> list[str]:
        """Return the handler name of each call, such as `composer` or `probe`."""
        found: list[str] = []
        for call in self.calls():
            arguments = call[1:]
            while arguments and arguments[0] == "-d":
                arguments = arguments[2:]
            found.append("probe" if arguments[0] == "-r" else Path(arguments[0]).name)
        return found
