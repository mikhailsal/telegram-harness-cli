"""The configuration of a browser component's production build (decision 0018).

The gate writes `vite.quality.mjs` into the run's directory. It imports the
component's `vite.config.*`, when there is one, and overrides what decides the
result: `envDir` is an empty directory of the run, so no `.env` file is read,
and the output goes to a new directory of the run with `emptyOutDir` and
Vite's manifest, so a committed or stale `dist/` is never used. The gate's
boundary plugin writes `boundaries.json`, the resolved `base` and what the
module graph crosses:

- each Node builtin that Vite externalizes for the browser, unless the
  `browser` field of the importer's package maps it; an npm package of the
  same name, such as `events`, resolves to a file and is allowed;
- each declared server module in the graph, with its importers;
- each module of a declared server package, with its importers outside that
  package, so a package that imports one is found too.

The plugin fails nothing itself: the core reports what it recorded.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from quality.boundedio import JsonValue

WRAPPER = "vite.quality.mjs"
REPORT = "boundaries.json"
CONFIG_FILES = tuple(f"vite.config.{ext}" for ext in ("js", "mjs", "ts", "cjs", "mts", "cts"))
EXTERNAL = "__vite-browser-external"
IMPORTS = """\
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { isBuiltin } from "node:module";
import { dirname, join, sep } from "node:path";
"""
PLUGIN_BODY = """\
const manifests = new Map();

function manifest(directory) {
  if (!manifests.has(directory)) {
    const file = join(directory, "package.json");
    const parent = dirname(directory);
    const found = existsSync(file)
      ? JSON.parse(readFileSync(file, "utf8"))
      : parent === directory
        ? {}
        : manifest(parent);
    manifests.set(directory, found);
  }
  return manifests.get(directory);
}

function mapped(source, importer) {
  const browser = manifest(dirname(importer)).browser;
  if (typeof browser !== "object" || browser === null) return false;
  return source in browser || source.replace(/^node:/, "") in browser;
}

function owner(id) {
  const marker = `${sep}node_modules${sep}`;
  const at = id.lastIndexOf(marker);
  if (at < 0) return null;
  const parts = id.slice(at + marker.length).split(sep);
  return parts[0].startsWith("@") ? `${parts[0]}/${parts[1]}` : parts[0];
}

const file = (id) => id.split("?")[0];

function boundaries() {
  const report = { base: "/", violations: [] };
  const servers = new Set(settings.serverModules);
  const packages = new Set(settings.serverPackages);
  const record = (kind, name, importer) => {
    report.violations.push({ kind, name, importer: file(importer) });
  };
  return {
    name: "quality-boundaries",
    enforce: "pre",
    configResolved(config) {
      report.base = config.base;
    },
    async resolveId(source, importer, options) {
      if (importer === undefined || !isBuiltin(source)) return null;
      const resolved = await this.resolve(source, importer, { ...options, skipSelf: true });
      const external = resolved === null || resolved.external || resolved.id.startsWith(EXTERNAL);
      if (external && !mapped(source, file(importer))) record("builtin", source, importer);
      return resolved;
    },
    buildEnd() {
      for (const id of this.getModuleIds()) {
        const name = owner(file(id));
        const server = servers.has(file(id));
        if (!server && (name === null || !packages.has(name))) continue;
        for (const importer of this.getModuleInfo(id)?.importers ?? []) {
          if (server) record("module", file(id), importer);
          else if (owner(file(importer)) !== name) record("package", name, importer);
        }
      }
      writeFileSync(settings.report, JSON.stringify(report));
    },
  };
}

export default async (env) => {
  const user = loaded.default;
  const base = (typeof user === "function" ? await user(env) : await user) ?? {};
  return {
    ...base,
    envDir: settings.envDir,
    plugins: [...(base.plugins ?? []), boundaries()],
    build: {
      ...base.build,
      outDir: settings.outDir,
      emptyOutDir: true,
      manifest: true,
      write: true,
      watch: null,
    },
  };
};
"""


def vite_config(directory: Path) -> Path | None:
    """Return the configuration file Vite would load from the component directory."""
    return next((directory / name for name in CONFIG_FILES if (directory / name).is_file()), None)


def wrapper(
    directory: Path,
    run: Path,
    *,
    servers: Iterable[Path],
    packages: Iterable[str],
) -> str:
    """Return the configuration module that builds the component in `directory` into `run`."""
    config = vite_config(directory)
    load = (
        f"import * as loaded from {json.dumps(str(config))};"
        if config is not None
        else "const loaded = {};"
    )
    modules: list[JsonValue] = []
    modules.extend(sorted(str(path) for path in servers))
    names: list[JsonValue] = []
    names.extend(sorted(packages))
    settings: dict[str, JsonValue] = {
        "outDir": str(run / "dist"),
        "envDir": str(run / "env"),
        "report": str(run / REPORT),
        "serverModules": modules,
        "serverPackages": names,
    }
    head = f"{IMPORTS}{load}\n\nconst EXTERNAL = {json.dumps(EXTERNAL)};\n"
    return f"{head}const settings = {json.dumps(settings, indent=2)};\n\n{PLUGIN_BODY}"
