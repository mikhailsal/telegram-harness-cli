import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, expect, it } from "vitest";
import type { CliDependencies } from "../../src/cli/context.js";
import { isRunning } from "../../src/cli/service-unit.js";
import { acquireDirectoryLock, type LockLease } from "../../src/core/lock.js";

// Regression: a bridge that systemd was stopping still held the folder lock,
// so plain init answered "Already initialized and running" and started nothing.
// Value: protects=a live lock owner counts as running unless it is the main
// process of a unit in "deactivating"; fails_when=a stopping bridge counts as
// running, or a foreground bridge is taken for a stopping one, or a failed
// query stops counting a live bridge; why_new=no test combined a live lock
// owner with the unit state; seam=none; no_claim=systemd is not run (exec is
// faked; QA probed a real unit)

let directory: string;
let lease: LockLease;
let calls: string[];
let timeouts: (number | undefined)[];

beforeEach(async () => {
  directory = await mkdtemp(join(tmpdir(), "thc-running-"));
  lease = await acquireDirectoryLock(directory);
  calls = [];
  timeouts = [];
});
afterEach(async () => {
  await lease.release();
  await rm(directory, { recursive: true, force: true });
});

// systemctl as init sees it: is-active exits 3 unless the unit is active.
function unit(state: string, mainPid: number | "fails"): CliDependencies {
  const exec: CliDependencies["exec"] = (file, args, options) => {
    calls.push([file, ...args].join(" "));
    timeouts.push(options?.timeout);
    if (args.includes("is-active"))
      return state === "active"
        ? Promise.resolve({ stdout: "active\n", stderr: "" })
        : Promise.reject(new Error("Command failed"));
    if (mainPid === "fails")
      return Promise.reject(new Error("systemd user bus unavailable"));
    return Promise.resolve({
      stdout: `MainPID=${String(mainPid)}\nActiveState=${state}\n`,
      stderr: "",
    });
  };
  return { env: {}, exec } as unknown as CliDependencies;
}

it("does not count a lock owner that systemd is stopping as running", async () => {
  expect(await isRunning(directory, unit("deactivating", process.pid))).toBe(
    false,
  );
  expect(calls[1]).toMatch(
    /^systemctl --user show thc-[0-9a-f]{24}\.service --property ActiveState --property MainPID$/,
  );
  // The query is bounded, so a stalled systemctl cannot hold init.
  expect(timeouts[1]).toBeGreaterThan(0);
});

it("keeps counting a live lock owner that the stopping unit does not own", async () => {
  // A foreground bridge while the unit waits for leftover processes.
  expect(await isRunning(directory, unit("deactivating", 0))).toBe(true);
  expect(await isRunning(directory, unit("deactivating", 1))).toBe(true);
  // The lock owner is the main process, but nothing is stopping the unit.
  expect(await isRunning(directory, unit("inactive", process.pid))).toBe(true);
  expect(await isRunning(directory, unit("failed", process.pid))).toBe(true);
  expect(await isRunning(directory, unit("deactivating", "fails"))).toBe(true);
});

it("needs no lock owner for an active service and none means stopped", async () => {
  expect(await isRunning(directory, unit("active", 0))).toBe(true);
  expect(calls).toHaveLength(1);
  await lease.release();
  expect(await isRunning(directory, unit("deactivating", process.pid))).toBe(
    false,
  );
  expect(await isRunning(directory, unit("inactive", 0))).toBe(false);
});
