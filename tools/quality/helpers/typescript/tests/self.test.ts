// The helper measures itself with the limits the gate applies to it.

import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "vitest";
import { runStructure } from "../src/structure.ts";
import { HELPER } from "./fixtures.ts";

const LIMITS = { complexity: 10, nesting: 3, parameters: 5, code_lines: 50 };
const FILE_LINES = 250;

function sources(directory: string): string[] {
  return readdirSync(join(HELPER, directory))
    .filter((name) => name.endsWith(".ts"))
    .map((name) => join(HELPER, directory, name));
}

test("every function of the helper stays within the structure limits", () => {
  const files = [...sources("src"), ...sources("tests")];
  const tsconfig = join(HELPER, "tsconfig.json");
  const request = { protocol: 1, typescript: HELPER, tsconfig, files };
  const over = runStructure(request).files.flatMap((report) => [
    ...(report.error === null
      ? []
      : [`${report.file}: ${report.error.message}`]),
    ...report.functions
      .filter((found) =>
        Object.entries(LIMITS).some(
          ([key, limit]) => found[key as keyof typeof LIMITS] > limit,
        ),
      )
      .map((found) => `${report.file}:${String(found.line)} ${found.name}`),
  ]);
  expect(over).toEqual([]);
});

test("every file of the helper stays within the file length limit", () => {
  const files = [...sources("src"), ...sources("tests")];
  const long = files.filter(
    (file) => readFileSync(file, "utf8").split("\n").length - 1 > FILE_LINES,
  );
  expect(long).toEqual([]);
});

test("every import between helper modules resolves", () => {
  const tsconfig = join(HELPER, "tsconfig.json");
  const files = sources("src");
  const request = { protocol: 1, typescript: HELPER, tsconfig, files };
  const local = runStructure(request).files.flatMap((report) =>
    report.imports.filter((found) => found.specifier.startsWith(".")),
  );
  expect(local.length).toBeGreaterThan(0);
  expect(local.filter((found) => found.resolved === null)).toEqual([]);
});
