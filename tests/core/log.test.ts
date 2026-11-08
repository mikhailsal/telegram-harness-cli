import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, expect, it, vi } from "vitest";
import { runtimeLog } from "../../src/core/log.js";

const roots: string[] = [];
afterEach(async () => {
  vi.restoreAllMocks();
  for (const root of roots.splice(0))
    await rm(root, { recursive: true, force: true });
});
async function directory() {
  const root = await mkdtemp(join(tmpdir(), "thc-log-"));
  roots.push(root);
  return root;
}
it("serializes log records without corrupting timestamps or leaking bot credentials", async () => {
  const root = await directory();
  const log = runtimeLog(root);
  log.write("one", {
    url: "https://api.telegram.org/bot123:synthetic/sendMessage",
    at: "invalid",
    event: "spoofed",
  });
  log.write("two", { token: "1:A" });
  await log.flush();
  const content = await readFile(join(root, ".thc", "bridge.log"), "utf8");
  expect(content).not.toContain("123:synthetic");
  expect(content).not.toContain("1:A");
  const rows = content
    .trim()
    .split("\n")
    .map((line) => JSON.parse(line) as { at: string; event: string });
  expect(rows.map((row) => row.event)).toEqual(["one", "two"]);
  expect(rows.every((row) => Number.isFinite(Date.parse(row.at)))).toBe(true);
});
it("rotates a full log before appending and reports filesystem failure without crashing", async () => {
  const root = await directory();
  await mkdir(join(root, ".thc"));
  await writeFile(
    join(root, ".thc", "bridge.log"),
    "x".repeat(5 * 1024 * 1024 + 1),
  );
  const log = runtimeLog(root);
  log.write("rotated");
  await log.flush();
  expect(
    (await readFile(join(root, ".thc", "bridge.log.1"))).length,
  ).toBeGreaterThan(5 * 1024 * 1024);
  await rm(join(root, ".thc", "bridge.log"));
  await mkdir(join(root, ".thc", "bridge.log"));
  const stderr = vi.spyOn(process.stderr, "write").mockReturnValue(true);
  log.write("blocked");
  await log.flush();
  expect(stderr).toHaveBeenCalled();
});
