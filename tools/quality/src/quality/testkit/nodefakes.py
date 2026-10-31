"""Fakes for TypeScript adapter tests: a Node that runs shell scripts, and installed packages.

The fake `node` hands its arguments to `sh`, so the tools and the helper are
shell scripts that print canned output and record how they were called; the
fake npm logs its working directory and arguments, then runs a replaceable
script. Unit tests thus run without Node or the npm cache.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from quality.adapters.typescript.environment import NPM_CLI, Node
from quality.policy import Component
from quality.testkit.policies import ComponentSpec, browser_spec, build_policy

PINS = {
    "typescript": "6.0.3",
    "eslint": "10.13.0",
    "@eslint/js": "10.0.1",
    "typescript-eslint": "8.72.0",
    "prettier": "3.9.10",
    "vitest": "5.0.5",
    "@vitest/coverage-v8": "5.0.5",
}
REGISTRY = "https://registry.npmjs.org"
ESLINT_CONFIG = 'export default [{ files: ["**/*.ts"] }];\n'


def typescript_component(root: str = "app") -> Component:
    """Return a TypeScript component `app` rooted at `root`."""
    spec = ComponentSpec(id="app", root=root, profile="typescript", capabilities=())
    (found,) = build_policy(spec).components
    return found


def browser_component(root: str = "web", extra: str = "") -> Component:
    """Return a React + Vite browser component `web` rooted at `root` (decision 0017)."""
    (found,) = build_policy(browser_spec(root=root, extra=extra)).components
    return found


def lockfile(pins: Mapping[str, str] = PINS, extra: Mapping[str, object] | None = None) -> str:
    """Render a lockfile that locks `pins` from the registry, plus `extra` entries."""
    packages: dict[str, object] = {"": {"name": "app", "devDependencies": dict(pins)}}
    for name, version in pins.items():
        resolved = f"{REGISTRY}/{name}/-/{name.rsplit('/', 1)[-1]}-{version}.tgz"
        packages[f"node_modules/{name}"] = {"version": version, "resolved": resolved}
    packages.update(extra or {})
    return json.dumps({"name": "app", "lockfileVersion": 3, "packages": packages})


def write_component(directory: Path, pins: Mapping[str, str] = PINS) -> Path:
    """Write a valid TypeScript component into `directory` and return it."""
    (directory / "src").mkdir(parents=True, exist_ok=True)
    (directory / "tests").mkdir(exist_ok=True)
    manifest = {"name": "app", "type": "module", "devDependencies": dict(pins)}
    (directory / "package.json").write_text(json.dumps(manifest) + "\n")
    (directory / "package-lock.json").write_text(lockfile(pins))
    (directory / "tsconfig.json").write_text('{"compilerOptions": {"strict": true}}\n')
    (directory / "eslint.config.js").write_text(ESLINT_CONFIG)
    return directory


def script(path: Path, body: str) -> Path:
    """Write an executable shell script and return its path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\n{body}")
    path.chmod(0o755)
    return path


def install_package(directory: Path, name: str, version: str, body: str) -> Path:
    """Install a fake package whose command-line script runs `body`; return the script."""
    package = directory / "node_modules" / name
    package.mkdir(parents=True, exist_ok=True)
    manifest = {"name": name, "version": version, "bin": {name.rsplit("/", 1)[-1]: "cli.sh"}}
    (package / "package.json").write_text(json.dumps(manifest))
    return script(package / "cli.sh", body)


def install_tools(
    directory: Path, bodies: Mapping[str, str], pins: Mapping[str, str] = PINS
) -> None:
    """Install fake Prettier, ESLint, and Vitest packages at the pinned versions."""
    for name, body in bodies.items():
        install_package(directory, name, pins[name], body)


@dataclass(frozen=True, slots=True)
class FakeNode:
    """A fake Node installation; its `npm ci` logs each call and then runs `npm.sh`."""

    node: Node
    root: Path

    @classmethod
    def create(cls, root: Path, cache: Path) -> FakeNode:
        """Install the fake Node under `root`, using `cache` as its npm cache."""
        executable = script(root / "bin" / "node", 'exec /bin/sh "$@"\n')
        log, after = root / "npm.log", root / "npm.sh"
        record = (
            f'for arg in "$PWD" "$@"; do printf \'%s\\t\' "$arg" >> \'{log}\'; done\n'
            f"echo >> '{log}'\nexec /bin/sh '{after}' \"$@\"\n"
        )
        script(root.joinpath(*NPM_CLI), record)
        script(after, "exit 0\n")
        return cls(Node(executable, cache), root)

    def npm(self, body: str) -> None:
        """Replace what the fake `npm ci` does after logging its call."""
        script(self.root / "npm.sh", body)

    def calls(self) -> list[list[str]]:
        """Return each `npm` call: its working directory, then its arguments."""
        log = self.root / "npm.log"
        lines = log.read_text().splitlines() if log.exists() else []
        return [line.split("\t")[:-1] for line in lines]
