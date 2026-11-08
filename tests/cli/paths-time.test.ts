import { mkdtempSync, mkdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, expect, it } from "vitest";
import { defaults } from "../../src/cli/context.js";
import { findDirectory } from "../../src/cli/paths.js";
import { formatTime, parseTime } from "../../src/cli/time.js";

let root: string;
beforeEach(() => {
  root = mkdtempSync(join(tmpdir(), "thc-paths-"));
});
afterEach(() => {
  rmSync(root, { recursive: true, force: true });
});
it("finds ancestor state and explicit agent and state directories", async () => {
  mkdirSync(join(root, ".thc"));
  mkdirSync(join(root, "nested"));
  expect(await findDirectory(join(root, "nested"))).toBe(root);
  expect(await findDirectory(root, root)).toBe(root);
  await expect(findDirectory(root, join(root, "absent"))).rejects.toMatchObject(
    { info: { code: "E_NO_STATE_DIR" } },
  );
});
it("round-trips timestamps in the configured timezone and rejects impossible wall times", () => {
  const original = "2026-11-07T15:00:00.000Z";
  const shown = formatTime(original, "Europe/Moscow");
  expect(parseTime(shown, "Europe/Moscow")).toBe(original);
  expect(parseTime(original, "UTC")).toBe(original);
  expect(() => parseTime("not a date", "UTC")).toThrow();
  expect(() => parseTime("2026-02-31 18:00:00", "UTC")).toThrow();
});
it("executes injected default command runner and propagates failures", async () => {
  const runtime = defaults();
  const result = await runtime.exec(process.execPath, [
    "-e",
    "process.stdout.write('ok')",
  ]);
  expect(result.stdout).toBe("ok");
  await expect(
    runtime.exec(process.execPath, ["-e", "process.exit(3)"]),
  ).rejects.toThrow();
});

it("handles missing state and .thc overrides", async () => {
  await expect(findDirectory(root)).rejects.toMatchObject({
    info: { code: "E_NO_STATE_DIR" },
  });
  mkdirSync(join(root, ".thc"));
  expect(await findDirectory(root, join(root, ".thc"))).toBe(root);
});
it("runs inherited commands and surfaces their failures", async () => {
  const runtime = defaults();
  runtime.stdout("");
  runtime.stderr("");
  expect(runtime.now()).toBeGreaterThan(0);
  await runtime.sleep(0);
  await expect(
    runtime.exec(process.execPath, ["-e", ""], { inherit: true }),
  ).resolves.toEqual({ stdout: "", stderr: "" });
  await expect(
    runtime.exec(process.execPath, ["-e", "process.exit(3)"], {
      inherit: true,
    }),
  ).rejects.toThrow();
});
