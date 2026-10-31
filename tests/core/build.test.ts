import {
  mkdtempSync,
  mkdirSync,
  readFileSync,
  rmSync,
  writeFileSync,
  existsSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, expect, it } from "vitest";
import { build } from "../../scripts/build.js";

const directories: string[] = [];
afterEach(() => {
  for (const directory of directories.splice(0))
    rmSync(directory, { recursive: true, force: true });
});
function fixture(script: string): string {
  const directory = mkdtempSync(join(tmpdir(), "thc-build-"));
  directories.push(directory);
  mkdirSync(join(directory, "node_modules/typescript/bin"), {
    recursive: true,
  });
  mkdirSync(join(directory, "dist"));
  writeFileSync(join(directory, "dist/stale"), "old");
  writeFileSync(join(directory, "node_modules/typescript/bin/tsc"), script);
  writeFileSync(join(directory, "package-lock.json"), "locked");
  return directory;
}
it("rebuilds the generated artifact and copies its lockfile", () => {
  const directory = fixture(
    "const fs = require('node:fs'); fs.mkdirSync('dist/cli', {recursive:true}); fs.writeFileSync('dist/cli/main.js', 'ready');",
  );
  build(directory);
  expect(existsSync(join(directory, "dist/stale"))).toBe(false);
  expect(readFileSync(join(directory, "dist/cli/main.js"), "utf8")).toBe(
    "ready",
  );
  expect(readFileSync(join(directory, "npm-shrinkwrap.json"), "utf8")).toBe(
    "locked",
  );
});
it("does not publish a lockfile when compilation fails", () => {
  const directory = fixture("process.exit(2)");
  expect(() => {
    build(directory);
  }).toThrow("TypeScript build failed (2)");
  expect(existsSync(join(directory, "npm-shrinkwrap.json"))).toBe(false);
});
