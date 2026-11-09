import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import type { EventEmitter } from "node:events";
import {
  mkdir,
  open,
  readFile,
  realpath,
  unlink,
  type FileHandle,
} from "node:fs/promises";
import { join, resolve } from "node:path";
import { z } from "zod";
import { atomicJson, isMissing, readJson } from "./state.ts";

const ownerSchema = z.strictObject({
  pid: z.number().int().positive(),
  instance: z.string(),
  start: z.string(),
  directory: z.string(),
});
export type LockOwner = z.infer<typeof ownerSchema>;
export interface LockLease {
  owner: LockOwner;
  lost: Promise<Error>;
  release: () => Promise<void>;
}

export class AlreadyRunningError extends Error {
  readonly code = "E_ALREADY_RUNNING";
  constructor() {
    super("A bridge already owns this folder; use thc status or thc attach");
    this.name = "AlreadyRunningError";
  }
}

async function processStart(pid: number): Promise<string> {
  const value = await readFile(`/proc/${String(pid)}/stat`, "utf8");
  const start = value.slice(value.lastIndexOf(")") + 2).split(" ")[19];
  if (!start) throw new Error("Cannot identify bridge process start time");
  return start;
}

/**
 * Whether a record names this folder, however its path was spelled when the
 * bridge started: a project reached through a symlink is one folder. A copied
 * `.thc` names another folder, and so does a recorded path that cannot be
 * resolved, exactly as when only the spelling was compared.
 */
async function namesFolder(
  recorded: string,
  directory: string,
): Promise<boolean> {
  if (recorded === resolve(directory)) return true;
  try {
    return (await realpath(recorded)) === (await realpath(directory));
  } catch {
    return false;
  }
}

export async function readLockOwner(
  directory: string,
): Promise<LockOwner | undefined> {
  try {
    const value: unknown = JSON.parse(
      await readFile(join(directory, ".thc", "lock-owner.json"), "utf8"),
    );
    const owner = ownerSchema.parse(value);
    if (
      !(await namesFolder(owner.directory, directory)) ||
      (await processStart(owner.pid)) !== owner.start
    )
      return;
    return owner;
  } catch (error) {
    if (
      isMissing(error) ||
      error instanceof z.ZodError ||
      error instanceof SyntaxError
    )
      return;
    throw error;
  }
}

async function lock(handle: FileHandle): Promise<void> {
  await new Promise<void>((accept, reject) => {
    const child = spawn("flock", ["-n", "-E", "73", "3"], {
      stdio: ["ignore", "ignore", "ignore", handle.fd],
    });
    child.once("error", reject);
    child.once("exit", (code) => {
      if (code === 0) accept();
      else
        reject(
          code === 73
            ? new AlreadyRunningError()
            : new Error(`flock exited ${String(code)}`),
        );
    });
  });
}

export async function acquireDirectoryLock(
  directory: string,
): Promise<LockLease> {
  const root = resolve(directory);
  const state = join(root, ".thc");
  await mkdir(state, { recursive: true, mode: 0o700 });
  const handle = await open(join(state, "lock"), "a+", 0o600);
  try {
    await lock(handle);
    const owner = {
      pid: process.pid,
      instance: randomUUID(),
      start: await processStart(process.pid),
      directory: root,
    };
    const path = join(state, "lock-owner.json");
    await atomicJson(path, owner);
    return makeLease(handle, owner, path);
  } catch (error) {
    await handle.close();
    throw error;
  }
}
function makeLease(
  handle: FileHandle,
  owner: LockOwner,
  path: string,
): LockLease {
  let releasing = false;
  let released: Promise<void> | undefined;
  const lost = new Promise<Error>((resolve) => {
    (handle as FileHandle & EventEmitter).once("close", () => {
      if (!releasing)
        resolve(new Error("Folder lock handle closed unexpectedly"));
    });
  });
  const release = (): Promise<void> => {
    if (released) return released;
    releasing = true;
    released = (async () => {
      try {
        const existing = await readJson(path, ownerSchema.nullable(), null);
        if (existing?.instance === owner.instance)
          await unlink(path).catch((error: unknown) => {
            if (!isMissing(error)) throw error;
          });
      } finally {
        await handle.close();
      }
    })();
    return released;
  };
  return { owner, lost, release };
}

export async function stopDirectory(directory: string): Promise<boolean> {
  const owner = await readLockOwner(directory);
  if (!owner) return false;
  process.kill(owner.pid, "SIGTERM");
  return true;
}
