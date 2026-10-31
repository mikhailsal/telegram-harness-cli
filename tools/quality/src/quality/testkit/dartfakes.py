"""Fakes for Dart adapter tests: an SDK whose `dart` runs shell scripts, and components.

The fake `dart` logs its working directory and arguments, drops
`--suppress-analytics`, and hands the rest to `handlers/<command>.sh`, or to
`handlers/dill.sh` when it runs a compiled program; a command without a
handler succeeds silently. The default `compile` handler writes the `-o` file.
Unit tests thus run without the SDK or the pub cache.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import yaml

from quality.adapters.dart.environment import Sdk
from quality.policy import Component
from quality.testkit.nodefakes import script
from quality.testkit.policies import ComponentSpec, build_policy

PINS = {"test": "1.33.0", "coverage": "1.16.0", "lints": "6.1.0"}
VERSION = "3.13.6"
OPTIONS = """\
include: package:lints/recommended.yaml
analyzer:
  language:
    strict-casts: true
    strict-inference: true
    strict-raw-types: true
"""
COMPILE = 'while [ $# -gt 0 ]; do [ "$1" = -o ] && printf kernel > "$2"; shift; done\n'


def dart_component(
    root: str = "app", source: tuple[str, ...] = ("lib",), integration: tuple[str, ...] = ()
) -> Component:
    """Return a Dart component `app` rooted at `root` with unit tests in `test`."""
    spec = ComponentSpec(
        id="app",
        root=root,
        profile="dart",
        source=source,
        unit=("test",),
        integration=integration,
        capabilities=(),
    )
    (found,) = build_policy(spec).components
    return found


def pubspec_lock(pins: Mapping[str, str] = PINS, extra: Mapping[str, object] | None = None) -> str:
    """Render a lockfile that locks `pins` from pub.dev, plus `extra` entries."""
    packages: dict[str, object] = {
        name: {
            "dependency": "direct dev",
            "description": {"name": name, "url": "https://pub.dev"},
            "source": "hosted",
            "version": version,
        }
        for name, version in pins.items()
    }
    packages.update(extra or {})
    return yaml.safe_dump({"packages": packages, "sdks": {"dart": ">=3.13.0 <4.0.0"}})


def pubspec(name: str = "calc", **fields: object) -> str:
    """Render a pubspec.yaml with the profile's packages as dev dependencies."""
    document: dict[str, object] = {"name": name, "environment": {"sdk": "^3.13.0"}}
    document["dev_dependencies"] = dict(PINS)
    document.update(fields)
    return yaml.safe_dump(document)


def write_component(directory: Path, name: str = "calc", pins: Mapping[str, str] = PINS) -> Path:
    """Write a valid Dart component into `directory` and return it."""
    (directory / "lib").mkdir(parents=True, exist_ok=True)
    (directory / "test").mkdir(exist_ok=True)
    (directory / "pubspec.yaml").write_text(pubspec(name))
    (directory / "pubspec.lock").write_text(pubspec_lock(pins))
    (directory / "analysis_options.yaml").write_text(OPTIONS)
    return directory


def write_helper(directory: Path) -> Path:
    """Write the files a Dart helper is built from into `directory` and return it."""
    (directory / "bin").mkdir(parents=True, exist_ok=True)
    (directory / "lib").mkdir(exist_ok=True)
    (directory / "pubspec.yaml").write_text("name: helper\n")
    (directory / "pubspec.lock").write_text("packages: {}\n")
    (directory / "bin" / "main.dart").write_text("void main() {}\n")
    (directory / "lib" / "cli.dart").write_text("void run() {}\n")
    return directory


@dataclass(frozen=True, slots=True)
class FakeDart:
    """A fake SDK; each `dart` call is logged and dispatched to a replaceable handler."""

    sdk: Sdk
    root: Path

    @classmethod
    def create(cls, root: Path, cache: Path) -> FakeDart:
        """Install the fake SDK under `root`, using `cache` as its pub cache."""
        log, handlers = root / "dart.log", root / "handlers"
        body = (
            f'for arg in "$PWD" "$@"; do printf \'%s\\t\' "$arg" >> \'{log}\'; done\n'
            f"echo >> '{log}'\n"
            '[ "$1" = --suppress-analytics ] && shift\n'
            f"case \"$1\" in *.dill) handler='{handlers}/dill.sh' ;; "
            f'*) handler="{handlers}/$1.sh" ;; esac\n'
            '[ -f "$handler" ] || exit 0\n'
            'exec /bin/sh "$handler" "$@"\n'
        )
        executable = script(root / "bin" / "dart", body)
        (root / "version").write_text(f"{VERSION}\n")
        fake = cls(Sdk(executable, cache), root)
        fake.handle("compile", COMPILE)
        return fake

    def handle(self, command: str, body: str) -> None:
        """Replace what the fake `dart <command>` does after logging its call."""
        script(self.root / "handlers" / f"{command}.sh", body)

    def calls(self) -> list[list[str]]:
        """Return each `dart` call: its working directory, then its arguments."""
        log = self.root / "dart.log"
        lines = log.read_text().splitlines() if log.exists() else []
        return [line.split("\t")[:-1] for line in lines]

    def commands(self) -> list[str]:
        """Return the command of each call, such as `pub` or `test`, or `dill` for a program."""
        found: list[str] = []
        for call in self.calls():
            arguments = [argument for argument in call[1:] if argument != "--suppress-analytics"]
            found.append("dill" if arguments[0].endswith(".dill") else arguments[0])
        return found
