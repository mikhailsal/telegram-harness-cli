import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { afterEach, expect, it } from "vitest";
import { stop } from "../../src/cli/admin.js";
import { defaults, type CliDependencies } from "../../src/cli/context.js";
import { readLockOwner } from "../../src/core/lock.js";

const children: ChildProcessWithoutNullStreams[] = [];
const roots: string[] = [];
afterEach(async () => {
  for (const child of children.splice(0))
    if (child.exitCode === null && child.signalCode === null)
      child.kill("SIGKILL");
  await Promise.all(
    roots.splice(0).map((root) => rm(root, { recursive: true, force: true })),
  );
});

/**
 * A bridge stand-in: holds the folder lock. On SIGTERM it releases the lock at
 * once and exits `shutdownMs` later (never when negative), like a bridge that
 * still flushes after its lease is gone. Resolves with the process id.
 */
function bridge(root: string, shutdownMs: number): Promise<number> {
  const module = pathToFileURL(join(process.cwd(), "src/core/lock.ts")).href;
  const code = `import { acquireDirectoryLock } from ${JSON.stringify(module)}; const lease = await acquireDirectoryLock(process.argv[1]); const delay = Number(process.argv[2]); process.on('SIGTERM', () => { if (delay >= 0) lease.release().then(() => setTimeout(() => process.exit(0), delay)); }); console.log('READY'); setInterval(() => {}, 1000);`;
  const child = spawn(
    process.execPath,
    ["--input-type=module", "-e", code, root, String(shutdownMs)],
    { stdio: ["pipe", "pipe", "pipe"] },
  );
  children.push(child);
  return new Promise((resolve, reject) => {
    child.once("error", reject);
    child.stdout.once("data", () => {
      resolve(child.pid ?? 0);
    });
    child.once("exit", (code) => {
      reject(new Error(`child exited ${String(code)}`));
    });
  });
}
async function fixture() {
  const root = await mkdtemp(join(tmpdir(), "thc-stop-"));
  roots.push(root);
  let output = "";
  const deps: CliDependencies = {
    ...defaults(),
    stdout: (value) => {
      output += value;
    },
  };
  return { root, deps, output: () => output };
}

it("waits for the bridge process to exit, not only for its lock to be released", async () => {
  const { root, deps, output } = await fixture();
  const pid = await bridge(root, 300);
  await stop(root, deps);
  expect(await readLockOwner(root)).toBeUndefined();
  expect(() => process.kill(pid, 0)).toThrow();
  expect(output()).toBe("Stop requested.\nStopped.\n");
});

it("fails instead of reporting a stop that did not happen", async () => {
  const { root, deps, output } = await fixture();
  await bridge(root, -1);
  let clock = 0;
  deps.now = () => (clock += 10_000);
  deps.sleep = () => Promise.resolve();
  await expect(stop(root, deps)).rejects.toMatchObject({
    info: { code: "E_ALREADY_RUNNING", fix: "thc status" },
  });
  expect(output()).toBe("Stop requested.\n");
  expect(await readLockOwner(root)).toBeDefined();
});

it("says so when no bridge runs", async () => {
  const { root, deps, output } = await fixture();
  await stop(root, deps);
  expect(output()).toBe("Bridge is not running.\n");
});
