"""A scratch React + Vite browser component, locked offline from the shared npm cache.

The component depends on the Node and browser pins of the core's toolchain
manifest. Resolving versions offline needs registry metadata that `npm ci`
does not cache, so its lockfile starts from the seed beside this module, which
resolves every pinned package, and npm fits the seed to the manifest. The
seed is test data: bootstrapping a project with the component downloads what
it locks into the npm cache, and no project of the template installs it.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Mapping
from pathlib import Path

from quality.adapters.typescript.environment import Node
from quality.testkit.browserscenarios import MANIFEST, SCENARIOS, SCENARIOS_FILE, manifest
from quality.testkit.typescriptproject import lock, package_json, pinned_packages, write
from quality.toolchain import load_manifest

SEED = Path(__file__).with_name("browserseed") / "package-lock.json"
RUNTIME = ("react", "react-dom")
COMPILER_OPTIONS: dict[str, object] = {
    "target": "es2023",
    "module": "esnext",
    "moduleResolution": "bundler",
    "jsx": "react-jsx",
    "strict": True,
    "noEmit": True,
    "allowImportingTsExtensions": True,
    "skipLibCheck": True,
    "types": ["vite/client"],
    "lib": ["es2023", "dom", "dom.iterable"],
}
TSCONFIG: dict[str, object] = {
    "compilerOptions": COMPILER_OPTIONS,
    "include": ["src", "tests", "e2e"],
}
ESLINT = """\
import js from "@eslint/js";
import jsxA11y from "eslint-plugin-jsx-a11y-x";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
  js.configs.recommended,
  tseslint.configs.strictTypeChecked,
  {
    languageOptions: {
      globals: globals.browser,
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
  },
  reactHooks.configs.flat.recommended,
  jsxA11y.configs.recommended,
);
"""
VITE = """\
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({ plugins: [react()] });
"""
INDEX = """\
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <link rel="icon" href="/icon.svg" />
    <title>Counter</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
"""
ICON = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 2 2"><circle r="1"/></svg>\n'
MAIN = """\
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App.tsx";

const root = document.getElementById("root");
if (root === null) {
  throw new Error("the page has no #root element");
}
createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
"""
APP = """\
import { useState } from "react";

import styles from "./App.module.css";

export function App() {
  const [count, setCount] = useState(0);
  return (
    <main className={styles.page}>
      <h1>Counter</h1>
      <button
        type="button"
        onClick={() => {
          setCount(count + 1);
        }}
      >
        Count {count}
      </button>
    </main>
  );
}
"""
STYLES = """\
.page {
  margin: 0 auto;
  max-width: 40rem;
}
"""
TESTS = """\
import { render, screen } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { expect, test } from "vitest";

import { App } from "../src/App.tsx";

test("the button counts its clicks", async () => {
  render(<App />);
  const button = screen.getByRole("button", { name: "Count 0" });
  await userEvent.click(button);
  expect(button.textContent).toBe("Count 1");
});
"""
# The entry point runs on import, so each test imports a fresh copy.
MAIN_TESTS = """\
import { act, screen } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

beforeEach(() => {
  vi.resetModules();
});

test("the page renders the counter into its root", async () => {
  document.body.innerHTML = '<div id="root"></div>';
  await act(async () => {
    await import("../src/main.tsx");
  });
  expect(screen.getByRole("button").textContent).toBe("Count 0");
});

test("a page without a root element does not start", async () => {
  document.body.innerHTML = "";
  await expect(import("../src/main.tsx")).rejects.toThrow(
    "the page has no #root element",
  );
});
"""
FILES = {
    "index.html": INDEX,
    "public/icon.svg": ICON,
    "src/main.tsx": MAIN,
    "src/App.tsx": APP,
    "src/App.module.css": STYLES,
    "tests/App.test.tsx": TESTS,
    "tests/main.test.tsx": MAIN_TESTS,
    SCENARIOS_FILE: SCENARIOS,
}


def browser_packages() -> dict[str, str]:
    """Return the npm packages a browser component locks: the browser and Node pins."""
    return {**load_manifest().packages["npm-browser"], **pinned_packages()}


def browser_project(
    directory: Path,
    files: Mapping[str, str] | None = None,
    *,
    name: str = "web",
    node: Node | None = None,
) -> Path:
    """Write and lock a browser component with `files` into `directory`; return it.

    By default it is the counter with its unit tests, its scenarios, and their manifest.
    """
    packages = browser_packages()
    runtime = {package: packages.pop(package) for package in RUNTIME}
    written = {
        "package.json": package_json(name, dependencies=runtime, devDependencies=packages),
        "tsconfig.json": json.dumps(TSCONFIG, indent=2) + "\n",
        "eslint.config.js": ESLINT,
        "vite.config.ts": VITE,
        **({**FILES, MANIFEST: manifest(name)} if files is None else files),
    }
    write(directory, written)
    shutil.copyfile(SEED, directory / "package-lock.json")
    lock(directory, node)
    return directory
