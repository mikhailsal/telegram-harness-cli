// Temporary fixture projects for the tests; nothing here is committed as source.

import {
  mkdirSync,
  mkdtempSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { onTestFinished } from "vitest";
import type { FunctionJson } from "../src/measure.ts";
import { runStructure, type FileReport } from "../src/structure.ts";

/** The helper's own directory, which resolves the pinned TypeScript and ESLint. */
export const HELPER = resolve(fileURLToPath(new URL("..", import.meta.url)));

/** Create a directory with the given files, removed when the test finishes. */
export function project(files: Readonly<Record<string, string>>): string {
  const root = mkdtempSync(join(tmpdir(), "quality-typescript-helper-"));
  onTestFinished(() => {
    rmSync(root, { recursive: true, force: true });
  });
  for (const [name, text] of Object.entries(files)) {
    mkdirSync(dirname(join(root, name)), { recursive: true });
    writeFileSync(join(root, name), text);
  }
  return root;
}

/** Let a fixture resolve the helper's packages. */
export function linkPackages(root: string): void {
  symlinkSync(join(HELPER, "node_modules"), join(root, "node_modules"), "dir");
}

/** The structure report of one file, with the given tsconfig or none. */
export function structureOf(file: string, tsconfig: string | null): FileReport {
  const request = { protocol: 1, typescript: HELPER, tsconfig, files: [file] };
  const [report] = runStructure(request).files;
  if (report === undefined) {
    throw new Error(`no report for ${file}`);
  }
  return report;
}

/** The functions of a snippet file, in report order. */
export function functionsOf(
  text: string,
  file = "snippet.ts",
): readonly FunctionJson[] {
  const root = project({ [file]: text });
  return structureOf(join(root, file), null).functions;
}

/** The qualified names of a snippet's functions, in report order. */
export function namesOf(text: string): string[] {
  return functionsOf(text).map((found) => found.name);
}

/** The functions of a snippet by name; a repeated name keeps its first function. */
export function byName(text: string): Map<string, FunctionJson> {
  const reversed = [...functionsOf(text)].reverse();
  return new Map(reversed.map((found) => [found.name, found]));
}

/** The first function of a snippet with this name. */
export function functionOf(text: string, name: string): FunctionJson {
  const found = byName(text).get(name);
  if (found === undefined) {
    throw new Error(`no function ${name}`);
  }
  return found;
}

/** Options that keep fixture programs small: one library file, unchecked. */
export const LEAN = { lib: ["es2024"], skipLibCheck: true, types: [] };

export function json(value: unknown): string {
  return JSON.stringify(value);
}
