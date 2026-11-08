import { join } from "node:path";
import { expect, test } from "vitest";
import { runStructure } from "../src/structure.ts";
import { HELPER, project, structureOf } from "./fixtures.ts";

test("a syntax error is reported with its line and no metrics", () => {
  const text = "const ok = 1;\nconst bad = ;\nconst fine = 2;\n";
  const root = project({ "broken.ts": text });
  const report = structureOf(join(root, "broken.ts"), null);
  expect(report.functions).toEqual([]);
  expect(report.imports).toEqual([]);
  expect(report.error?.line).toBe(2);
  expect(report.error?.message).toBe("Expression expected.");
});

test("JSX in a .ts file is a syntax error, in a .tsx file it is not", () => {
  const text = "export const view = () => <div />;\n";
  const root = project({ "a.ts": text, "a.tsx": text });
  expect(structureOf(join(root, "a.ts"), null).error).not.toBeNull();
  expect(structureOf(join(root, "a.tsx"), null).error).toBeNull();
});

test("an unreadable file is reported without a line", () => {
  const report = structureOf(join(HELPER, "missing.ts"), null);
  expect(report.error?.line).toBeNull();
  expect(report.error?.message).toMatch(/^cannot analyze the file: ENOENT/);
});

test("a file too deeply nested for the parser is reported", () => {
  const depth = 200_000;
  const text = `export const x = ${"(".repeat(depth)}1${")".repeat(depth)};\n`;
  const root = project({ "deep.ts": text });
  const report = structureOf(join(root, "deep.ts"), null);
  expect(report.error?.line).toBeNull();
  expect(report.error?.message).toMatch(/call stack/);
});

test("files are reported in request order", () => {
  const root = project({ "b.ts": "function b() {}\n", "a.ts": "" });
  const files = [join(root, "b.ts"), join(root, "a.ts")];
  const request = { protocol: 1, typescript: HELPER, tsconfig: null, files };
  const response = runStructure(request);
  expect(response.files.map((report) => report.file)).toEqual(files);
  expect(response.files[0]?.functions).toEqual([
    {
      name: "b",
      line: 1,
      code_lines: 0,
      complexity: 1,
      nesting: 0,
      parameters: 0,
    },
  ]);
});
