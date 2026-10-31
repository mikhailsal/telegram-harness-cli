// The helper has no runtime dependencies: TypeScript and ESLint are loaded from
// the component being checked, so the versions its lockfile pins do the work.

import { createRequire } from "node:module";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import type { ESLint } from "eslint";
import type * as ts from "typescript";
import { describe, field, isRecord, RequestError } from "./protocol.ts";

export type TypeScript = typeof ts;
export type EslintClass = typeof ESLint;

function resolver(base: string): NodeJS.Require {
  return createRequire(join(base, "package.json"));
}

function isTypeScript(value: unknown): value is TypeScript {
  return isRecord(value) && typeof value.createProgram === "function";
}

function isEslintClass(value: unknown): value is EslintClass {
  return typeof value === "function";
}

/** Load the `typescript` package that `base` resolves. */
export function loadTypeScript(base: string): TypeScript {
  let loaded: unknown;
  try {
    loaded = resolver(base)("typescript");
  } catch (error) {
    throw new RequestError(
      `cannot load typescript from ${base}: ${describe(error)}`,
    );
  }
  if (!isTypeScript(loaded)) {
    throw new RequestError(`typescript in ${base} is not the compiler API`);
  }
  return loaded;
}

/** Load the `ESLint` class of the `eslint` package that `base` resolves. */
export async function loadEslint(base: string): Promise<EslintClass> {
  let loaded: unknown;
  try {
    loaded = await import(pathToFileURL(resolver(base).resolve("eslint")).href);
  } catch (error) {
    throw new RequestError(
      `cannot load eslint from ${base}: ${describe(error)}`,
    );
  }
  const found = field(loaded, "ESLint");
  if (!isEslintClass(found)) {
    throw new RequestError(`eslint in ${base} does not export ESLint`);
  }
  return found;
}
