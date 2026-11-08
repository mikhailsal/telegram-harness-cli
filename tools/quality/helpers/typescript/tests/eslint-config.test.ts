import { join } from "node:path";
import { expect, test } from "vitest";
import { runEslintConfig } from "../src/eslint-config.ts";
import { HELPER, json, linkPackages, project } from "./fixtures.ts";

const CONFIG = `import tseslint from "typescript-eslint";

const parse = () => ({});

export default [
  { ignores: ["ignored/**"] },
  {
    files: ["typed/**/*.ts"],
    languageOptions: {
      parser: tseslint.parser,
      parserOptions: { projectService: true },
    },
  },
  {
    files: ["project/**/*.ts"],
    languageOptions: {
      parser: tseslint.parser,
      parserOptions: { project: "./tsconfig.json" },
    },
  },
  { files: ["plain/**/*.ts"], languageOptions: { parser: tseslint.parser } },
  { files: ["bare/**/*.ts"], languageOptions: { parser: { parse } } },
  { files: ["nameless/**/*.ts"], languageOptions: { parser: { meta: {}, parse } } },
];
`;

const PARSER = "typescript-eslint/parser";

/** Directory, then configured, ignored, typed, and the parser's name. */
const EXPECTED = [
  ["typed", true, false, true, PARSER],
  ["project", true, false, true, PARSER],
  ["plain", true, false, false, PARSER],
  ["bare", true, false, false, null],
  ["nameless", true, false, false, null],
  ["ignored", false, true, false, null],
  ["other", false, true, false, null],
] as const;

test("each file is described by the configuration ESLint computes for it", async () => {
  const root = project({
    "package.json": json({ type: "module" }),
    "eslint.config.js": CONFIG,
  });
  linkPackages(root);
  const files = EXPECTED.map(([name]) => join(root, name, "a.ts"));
  const request = { protocol: 1, eslint: HELPER, cwd: root, files };
  const response = await runEslintConfig(request);
  expect(response.files).toEqual(
    EXPECTED.map(([name, configured, ignored, typed, parser]) => ({
      file: join(root, name, "a.ts"),
      configured,
      ignored,
      typed,
      parser,
      rules: [],
    })),
  );
});

test("the default parser of a JavaScript file has no meta name", async () => {
  const root = project({ "eslint.config.js": "export default [{}];\n" });
  const file = join(root, "a.js");
  const response = await runEslintConfig({
    protocol: 1,
    eslint: HELPER,
    cwd: root,
    files: [file],
  });
  expect(response.files[0]).toEqual({
    file,
    configured: true,
    ignored: false,
    typed: false,
    parser: null,
    rules: [],
  });
});

const RULES = `const rule = { create: () => ({}) };
const plugin = { rules: { a: rule, b: rule, c: rule, d: rule } };

export default [
  {
    files: ["**/*.ts"],
    plugins: { p: plugin },
    rules: { "p/a": "error", "p/b": ["warn"], "p/c": "off", "p/d": 2 },
  },
  { files: ["tests/**"], rules: { "p/a": "off" } },
];
`;

test("each requested rule is reported with the severity the file gets", async () => {
  const root = project({ "eslint.config.js": RULES });
  const files = [join(root, "src/a.ts"), join(root, "tests/a.ts")];
  const rules = ["p/a", "p/b", "p/c", "p/d", "p/missing"];
  const response = await runEslintConfig({
    protocol: 1,
    eslint: HELPER,
    cwd: root,
    files,
    rules,
  });
  const severities = response.files.map((file) =>
    file.rules.map(({ rule, severity }) => `${rule}=${String(severity)}`),
  );
  expect(severities).toEqual([
    ["p/a=2", "p/b=1", "p/c=0", "p/d=2", "p/missing=0"],
    ["p/a=0", "p/b=1", "p/c=0", "p/d=2", "p/missing=0"],
  ]);
});
