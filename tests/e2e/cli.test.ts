import { spawnSync } from "node:child_process";
import { mkdtempSync, rmSync, symlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { afterEach, expect, it } from "vitest";

const directories: string[] = [];
const executable = resolve("dist/cli/main.js");
afterEach(() => {
  for (const directory of directories.splice(0))
    rmSync(directory, { recursive: true, force: true });
});
function command(args: string[], input?: string) {
  const directory = mkdtempSync(join(tmpdir(), "thc-built-cli-"));
  directories.push(directory);
  return spawnSync(process.execPath, [executable, ...args], {
    cwd: directory,
    input,
    encoding: "utf8",
    timeout: 10_000,
  });
}
it("the installed CLI artifact starts and explains its commands", () => {
  const result = command(["--help"]);
  expect(result.status).toBe(0);
  expect(result.stdout).toContain("thc");
  expect(result.stdout).toContain("init");
  expect(result.stdout).toContain("doctor");
});
it("starts through the symbolic link created for an npm executable", () => {
  const directory = mkdtempSync(join(tmpdir(), "thc-npm-link-"));
  directories.push(directory);
  const alias = join(directory, "thc");
  symlinkSync(executable, alias);
  const result = spawnSync(process.execPath, [alias, "--help"], {
    cwd: directory,
    encoding: "utf8",
  });
  expect(result.status).toBe(0);
  expect(result.stdout).toContain("init");
});
it("the built CLI rejects unknown commands with a useful nonzero result", () => {
  const result = command(["unknown-command"]);
  expect(result.status).not.toBe(0);
  expect(`${result.stdout}${result.stderr}`).toMatch(/E_|unknown|Unknown/);
});
it("the built renderer removes hidden content without removing code literals", () => {
  const result = command(
    ["render"],
    "visible\n\n<internal-note>hidden-value</internal-note>\n\n`<internal-note>literal</internal-note>`",
  );
  expect(result.status).toBe(0);
  expect(result.stdout).toContain("visible");
  expect(result.stdout).not.toContain("hidden-value");
  expect(result.stdout).toContain("literal");
});
