import { join } from "node:path";
import { expect, test } from "vitest";
import { runTypes, type TypesResponse } from "../src/typecheck.ts";
import { HELPER, json, LEAN, project } from "./fixtures.ts";

function typesOf(root: string): TypesResponse {
  const tsconfig = join(root, "tsconfig.json");
  return runTypes({ protocol: 1, typescript: HELPER, tsconfig });
}

const COMMON = { ...LEAN, strict: true, module: "nodenext" };

const GRAPH = {
  "package.json": json({ type: "module" }),
  "tsconfig.json": json({ files: [], references: [{ path: "./app" }] }),
  "core/tsconfig.json": json({
    compilerOptions: {
      ...COMMON,
      composite: true,
      outDir: "dist",
      rootDir: "src",
    },
    include: ["src"],
  }),
  "core/src/index.ts":
    "export function double(n: number): number {\n  return n * 2;\n}\n",
  "app/tsconfig.json": json({
    compilerOptions: {
      ...COMMON,
      noEmit: true,
      noUnusedLocals: true,
      paths: { "@core/*": ["../core/src/*"] },
    },
    include: ["src"],
    references: [{ path: "../core" }],
  }),
  "app/src/main.ts": [
    'import { double } from "../../core/src/index.js";',
    'import { double as twice } from "@core/index.js";',
    "export const four: number = double(2) + twice(1);",
    "",
  ].join("\n"),
};

test("referenced projects are checked from source without build output", () => {
  const root = project(GRAPH);
  const response = typesOf(root);
  expect(response.diagnostics).toEqual([]);
  expect(response.projects.map((found) => found.config)).toEqual([
    join(root, "tsconfig.json"),
    join(root, "app/tsconfig.json"),
    join(root, "core/tsconfig.json"),
  ]);
  expect(response.projects[0]).toMatchObject({ files: [], problems: [] });
  expect(response.projects[1]?.files).toEqual(
    expect.arrayContaining([
      join(root, "app/src/main.ts"),
      join(root, "core/src/index.ts"),
    ]),
  );
  expect(response.projects[2]?.files).toEqual([
    join(root, "core/src/index.ts"),
  ]);
});

test("an error in a referenced project is reported once, by its project", () => {
  const root = project({
    ...GRAPH,
    "core/src/index.ts":
      "export function double(n: number): number {\n  const unused = 1;\n  return `${n}`;\n}\n",
  });
  const codes = typesOf(root).diagnostics.map((found) => [
    found.file,
    found.code,
  ]);
  expect(codes).toEqual([[join(root, "core/src/index.ts"), 2322]]);
});

const SIBLINGS = (first: string, second: string) => ({
  "tsconfig.json": json({
    files: [],
    references: [{ path: `./${first}` }, { path: `./${second}` }],
  }),
  "plain/tsconfig.json": json({
    compilerOptions: { ...LEAN, strict: true },
    files: ["main.ts", "../shared.ts"],
  }),
  "picky/tsconfig.json": json({
    compilerOptions: { ...LEAN, strict: true, noUnusedLocals: true },
    files: ["main.ts", "../shared.ts"],
  }),
  "plain/main.ts": 'import "../extra";\nexport const p = 1;\n',
  "picky/main.ts": 'import "../extra";\nexport const q = 1;\n',
  "shared.ts":
    "export function f(): number {\n  const unused = 1;\n  return 2;\n}\n",
  "extra.ts": 'export const wrong: number = "x";\n',
});

test("a file listed by two projects is checked by the first one only", () => {
  const plainFirst = typesOf(project(SIBLINGS("plain", "picky"))).diagnostics;
  expect(plainFirst.map((found) => found.code)).toEqual([2322]);
  const pickyFirst = typesOf(project(SIBLINGS("picky", "plain"))).diagnostics;
  expect(pickyFirst.map((found) => found.code).sort()).toEqual([2322, 6133]);
});

test("a reference cycle visits each project once", () => {
  const root = project({
    "tsconfig.json": json({ files: [], references: [{ path: "./a" }] }),
    "a/tsconfig.json": json({
      compilerOptions: { ...LEAN, strict: true, composite: true },
      include: ["."],
      references: [{ path: "../b" }],
    }),
    "b/tsconfig.json": json({
      compilerOptions: { ...LEAN, strict: true, composite: true },
      include: ["."],
      references: [{ path: "../a" }],
    }),
    "a/a.ts": "export const a = 1;\n",
    "b/b.ts": "export const b = 1;\n",
  });
  const response = typesOf(root);
  expect(response.projects.map((found) => found.files.length)).toEqual([
    0, 1, 1,
  ]);
});

test("a missing referenced project is reported", () => {
  const root = project({
    "tsconfig.json": json({
      compilerOptions: { ...LEAN, strict: true },
      include: ["a.ts"],
      references: [{ path: "./gone" }],
    }),
    "a.ts": "export const a = 1;\n",
  });
  const response = typesOf(root);
  expect(response.projects[1]).toEqual({
    config: join(root, "gone/tsconfig.json"),
    files: [],
    problems: [],
  });
  expect(response.diagnostics.some((found) => found.file === null)).toBe(true);
});
