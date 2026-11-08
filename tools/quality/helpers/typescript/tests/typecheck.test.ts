import { join } from "node:path";
import { expect, test } from "vitest";
import { runTypes, strictness, type TypesResponse } from "../src/typecheck.ts";
import { HELPER, json, LEAN, linkPackages, project } from "./fixtures.ts";

function typesOf(root: string, tsconfig = "tsconfig.json"): TypesResponse {
  const request = {
    protocol: 1,
    typescript: HELPER,
    tsconfig: join(root, tsconfig),
  };
  return runTypes(request);
}

const STRICT = { compilerOptions: { ...LEAN, strict: true }, include: ["src"] };

test("strict must be on, and no flag of its family off", () => {
  expect(strictness({})).toEqual(['"strict" is not enabled']);
  expect(strictness({ strict: false })).toEqual(['"strict" is not enabled']);
  expect(
    strictness({ strict: true, noImplicitAny: false, strictNullChecks: false }),
  ).toEqual(['"noImplicitAny" is disabled', '"strictNullChecks" is disabled']);
  expect(strictness({ strict: true, alwaysStrict: true })).toEqual([]);
});

test("a type error is reported with its position", () => {
  const root = project({
    "tsconfig.json": json(STRICT),
    "src/a.ts": 'export const ok = 1;\nexport const n: number = "x";\n',
  });
  const response = typesOf(root);
  expect(response.projects).toEqual([
    {
      config: join(root, "tsconfig.json"),
      files: [join(root, "src/a.ts")],
      problems: [],
    },
  ]);
  expect(response.diagnostics).toEqual([
    {
      file: join(root, "src/a.ts"),
      line: 2,
      column: 14,
      code: 2322,
      category: "error",
      message: "Type 'string' is not assignable to type 'number'.",
    },
  ]);
});

test("a project that is not strict is reported, through extends too", () => {
  const root = project({
    "base.json": json({ compilerOptions: { ...LEAN, strict: true } }),
    "loose.json": json({ compilerOptions: { ...LEAN, noImplicitThis: false } }),
    "tsconfig.json": json({ extends: "./base.json", include: ["src"] }),
    "loose/tsconfig.json": json({ extends: "../loose.json", include: ["."] }),
    "src/a.ts": "export const a = 1;\n",
    "loose/b.ts": "export const b = 1;\n",
  });
  expect(typesOf(root).projects[0]?.problems).toEqual([]);
  expect(typesOf(root, "loose/tsconfig.json").projects[0]?.problems).toEqual([
    '"strict" is not enabled',
    '"noImplicitThis" is disabled',
  ]);
});

test("library and package declarations are not files of the project", () => {
  const root = project({
    "package.json": json({ type: "module" }),
    "tsconfig.json": json({
      compilerOptions: {
        strict: true,
        module: "nodenext",
        skipLibCheck: true,
        types: ["node"],
      },
      include: ["src"],
    }),
    "src/a.ts": 'import { expect } from "vitest";\nexport const e = expect;\n',
  });
  linkPackages(root);
  const response = typesOf(root);
  expect(response.diagnostics).toEqual([]);
  expect(response.projects[0]?.files).toEqual([join(root, "src/a.ts")]);
});

test("configuration errors are diagnostics of the config file", () => {
  const root = project({
    "tsconfig.json": json({
      compilerOptions: { ...LEAN, strict: true, bogus: 1 },
    }),
    "a.ts": "export const a = 1;\n",
  });
  const [diagnostic] = typesOf(root).diagnostics;
  expect(diagnostic?.file).toBe(join(root, "tsconfig.json"));
  expect(diagnostic?.code).toBe(5023);
  expect(diagnostic?.line).toBe(1);
});

test("an unreadable config is a diagnostic without a file", () => {
  const root = project({});
  const response = typesOf(root, "missing.json");
  expect(response.projects).toEqual([
    { config: join(root, "missing.json"), files: [], problems: [] },
  ]);
  expect(response.diagnostics[0]).toMatchObject({
    file: null,
    line: null,
    column: null,
    category: "error",
  });
});

test("diagnostics without a file are kept", () => {
  const root = project({
    "tsconfig.json": json({ compilerOptions: { strict: true, noLib: true } }),
    "a.ts": "export const a = 1;\n",
  });
  const codes = typesOf(root).diagnostics.map((found) => [
    found.file,
    found.code,
  ]);
  expect(codes).toContainEqual([null, 2318]);
});
