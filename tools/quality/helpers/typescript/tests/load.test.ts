import { expect, test } from "vitest";
import { loadEslint, loadTypeScript } from "../src/load.ts";
import { RequestError } from "../src/protocol.ts";
import { HELPER, project } from "./fixtures.ts";

const FAKE = (name: string, body: string) => ({
  [`node_modules/${name}/package.json`]: JSON.stringify({
    name,
    main: "index.js",
  }),
  [`node_modules/${name}/index.js`]: body,
});

test("typescript is loaded from the base directory", () => {
  const api = loadTypeScript(HELPER);
  expect(api.version).toBe("6.0.3");
});

test("a base without typescript is a bad request", () => {
  const root = project({ "package.json": "{}" });
  expect(() => loadTypeScript(root)).toThrow(RequestError);
  expect(() => loadTypeScript(root)).toThrow(/cannot load typescript from/);
});

test("a typescript package without the compiler API is a bad request", () => {
  const root = project(FAKE("typescript", "module.exports = { version: 1 };"));
  expect(() => loadTypeScript(root)).toThrow("is not the compiler API");
});

test("eslint is loaded from the base directory", async () => {
  const ESLint = await loadEslint(HELPER);
  expect(ESLint.version).toBe("10.13.0");
});

test("a base without eslint is a bad request", async () => {
  const root = project({ "package.json": "{}" });
  await expect(loadEslint(root)).rejects.toThrow(/cannot load eslint from/);
});

test("an eslint package without the ESLint class is a bad request", async () => {
  const root = project(FAKE("eslint", "module.exports = { ESLint: 1 };"));
  await expect(loadEslint(root)).rejects.toThrow("does not export ESLint");
});
