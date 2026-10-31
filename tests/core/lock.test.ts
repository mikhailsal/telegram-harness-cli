import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { expect, it, vi } from "vitest";
import type { FileHandle } from "node:fs/promises";

const captured = vi.hoisted(() => ({
  handle: undefined as FileHandle | undefined,
}));
vi.mock("node:fs/promises", async (importOriginal) => {
  const original = await importOriginal<typeof import("node:fs/promises")>();
  return {
    ...original,
    open: async (...args: Parameters<typeof original.open>) => {
      const handle = await original.open(...args);
      if (typeof args[0] === "string" && args[0].endsWith("/.thc/lock"))
        captured.handle = handle;
      return handle;
    },
  };
});
import {
  acquireDirectoryLock,
  AlreadyRunningError,
  readLockOwner,
  stopDirectory,
} from "../../src/core/lock.ts";
function childHolder(root: string): Promise<ChildProcessWithoutNullStreams> {
  const module = pathToFileURL(join(process.cwd(), "src/core/lock.ts")).href;
  const code = `import { acquireDirectoryLock } from ${JSON.stringify(module)}; await acquireDirectoryLock(process.argv[1]); console.log('READY'); setInterval(() => {}, 1000);`;
  const child = spawn(
    process.execPath,
    ["--input-type=module", "-e", code, root],
    { stdio: ["pipe", "pipe", "pipe"] },
  );
  return new Promise((resolve, reject) => {
    child.once("error", reject);
    child.stdout.once("data", () => {
      resolve(child);
    });
    child.once("exit", (code) => {
      reject(new Error(`child exited ${String(code)}`));
    });
  });
}
async function exited(child: ChildProcessWithoutNullStreams): Promise<void> {
  await new Promise<void>((resolve) =>
    child.once("exit", () => {
      resolve();
    }),
  );
}
it("holds an exclusive flock and waits for release before immediate reacquisition", async () => {
  const root = await mkdtemp(join(tmpdir(), "thc-lock-"));
  try {
    const first = await acquireDirectoryLock(root);
    expect((await readLockOwner(root))?.instance).toBe(first.owner.instance);
    await expect(acquireDirectoryLock(root)).rejects.toBeInstanceOf(
      AlreadyRunningError,
    );
    await Promise.all([first.release(), first.release()]);
    const next = await acquireDirectoryLock(root);
    expect(next.owner.instance).not.toBe(first.owner.instance);
    await next.release();
    expect(await readLockOwner(root)).toBeUndefined();
    await writeFile(
      join(root, ".thc", "lock-owner.json"),
      JSON.stringify({ ...first.owner, start: "reused-pid" }),
    );
    expect(await readLockOwner(root)).toBeUndefined();
    expect(await stopDirectory(root)).toBe(false);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
it("releases a killed owner and only signals the owned child", async () => {
  const root = await mkdtemp(join(tmpdir(), "thc-lock-"));
  let child: ChildProcessWithoutNullStreams | undefined;
  try {
    child = await childHolder(root);
    expect((await readLockOwner(root))?.pid).toBe(child.pid);
    const done = exited(child);
    child.kill("SIGKILL");
    await done;
    const next = await acquireDirectoryLock(root);
    await next.release();
    child = await childHolder(root);
    const stopped = exited(child);
    expect(await stopDirectory(root)).toBe(true);
    await stopped;
    const final = await acquireDirectoryLock(root);
    await final.release();
  } finally {
    if (child?.exitCode === null && child.signalCode === null)
      child.kill("SIGKILL");
    await rm(root, { recursive: true, force: true });
  }
});

it("leaves a different owner record intact when releasing", async () => {
  const root = await mkdtemp(join(tmpdir(), "thc-lock-owner-"));
  try {
    const lease = await acquireDirectoryLock(root);
    const path = join(root, ".thc", "lock-owner.json");
    const replacement = { ...lease.owner, instance: "replacement" };
    await writeFile(path, JSON.stringify(replacement));
    await lease.release();
    expect(JSON.parse(await readFile(path, "utf8"))).toEqual(replacement);
    const next = await acquireDirectoryLock(root);
    await next.release();
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
it("reports an unexpectedly closed lock handle", async () => {
  const root = await mkdtemp(join(tmpdir(), "thc-lock-lost-"));
  try {
    const lease = await acquireDirectoryLock(root);
    if (!captured.handle) throw new Error("Lock handle was not captured");
    await captured.handle.close();
    expect((await lease.lost).message).toContain("handle closed unexpectedly");
    await lease.release();
    const next = await acquireDirectoryLock(root);
    await next.release();
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
