"""A scratch TypeScript component, locked offline from the shared npm cache.

The component pins the profile's packages at the versions of the core's
toolchain manifest. The pinned npm writes its `package-lock.json` with
`--package-lock-only --offline`, so nothing is downloaded. Bootstrapping this
repository installs the TypeScript helper, which locks the same packages, but
`npm ci` caches only their tarballs, not the registry metadata that resolving
versions needs. A component without a lockfile therefore starts from the
helper's lockfile, which resolves every pinned package, and npm fits it to the
component's manifest.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from collections.abc import Mapping
from pathlib import Path

from quality.adapters.typescript import helper
from quality.adapters.typescript.environment import Node
from quality.process import Command, run
from quality.toolchain import cache_root, load_manifest, locate

LOCK_TIMEOUT = 600.0
SEED = helper.CORE / "helpers" / "typescript" / "package-lock.json"
COMPILER_OPTIONS: dict[str, object] = {
    "target": "es2024",
    "module": "nodenext",
    "moduleResolution": "nodenext",
    "strict": True,
    "noEmit": True,
    "allowImportingTsExtensions": True,
    "skipLibCheck": True,
}
TSCONFIG: dict[str, object] = {"compilerOptions": COMPILER_OPTIONS, "include": ["src", "tests"]}
ESLINT = """\
import js from "@eslint/js";
import tseslint from "typescript-eslint";

export default tseslint.config(
  js.configs.recommended,
  tseslint.configs.strictTypeChecked,
  {
    languageOptions: {
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
  },
);
"""
CORE = """\
export function sign(value: number): number {
  if (value < 0) {
    return -1;
  }
  return 1;
}
"""
TESTS = """\
import { expect, test } from "vitest";

import { sign } from "../src/core.ts";

test("sign", () => {
  expect(sign(-3)).toBe(-1);
  expect(sign(4)).toBe(1);
});
"""


def pinned_packages() -> dict[str, str]:
    """Return the npm packages the TypeScript profile pins, at their versions."""
    return dict(load_manifest().packages["npm"])


def pinned_node() -> Node:
    """Return the installed pinned Node with the shared npm cache."""
    root = cache_root()
    return Node(locate("node", load_manifest().tools["node"], root), root.parent / "npm")


def package_json(name: str, *, module: bool = True, **fields: object) -> str:
    """Return a package manifest; `module` makes `.ts` files ES modules."""
    document: dict[str, object] = {"name": name, "version": "0.0.0", "private": True}
    if module:
        document["type"] = "module"
    document.update(fields)
    return json.dumps(document, indent=2) + "\n"


def write(directory: Path, files: Mapping[str, str]) -> None:
    """Write text files below `directory`."""
    for path, text in files.items():
        target = directory / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")


def lock(directory: Path, node: Node | None = None) -> Path:
    """Write the lockfile of the package in `directory` from the cache; return its path."""
    found = node or pinned_node()
    target = directory / "package-lock.json"
    if not target.exists():
        shutil.copyfile(SEED, target)
    with tempfile.TemporaryDirectory(prefix="quality-npm-") as scratch:
        home = Path(scratch)
        user, shared = home / "npmrc", home / "npmrc-global"
        for empty in (user, shared):
            empty.write_text("", encoding="utf-8")
        argv = (str(found.executable), str(found.npm), "install", "--package-lock-only")
        argv += ("--offline", "--ignore-scripts", "--no-audit", "--no-fund", "--loglevel=error")
        argv += (f"--cache={found.cache}", f"--userconfig={user}", f"--globalconfig={shared}")
        command = Command(argv, cwd=directory, timeout=LOCK_TIMEOUT, env=found.environ(home))
        completed = run(command)
    if not completed.ok:
        msg = f"npm could not lock {directory}: {completed.stderr_text().strip()}"
        raise AssertionError(msg)
    return target


def typescript_project(
    directory: Path,
    files: Mapping[str, str] | None = None,
    *,
    name: str = "web",
    module: bool = True,
    node: Node | None = None,
) -> Path:
    """Write and lock a complete component with `files` into `directory`; return it."""
    written = {
        "package.json": package_json(name, module=module, devDependencies=pinned_packages()),
        "tsconfig.json": json.dumps(TSCONFIG, indent=2) + "\n",
        # A CommonJS package names its ES-module configuration `.mjs`.
        ("eslint.config.js" if module else "eslint.config.mjs"): ESLINT,
        **({"src/core.ts": CORE, "tests/core.test.ts": TESTS} if files is None else files),
    }
    write(directory, written)
    lock(directory, node)
    return directory
