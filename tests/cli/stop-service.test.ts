import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { afterEach, expect, it } from "vitest";
import { stop } from "../../src/cli/admin.js";
import { defaults, type CliDependencies } from "../../src/cli/context.js";
import { requestServiceStop } from "../../src/cli/service-unit.js";

// Regression (#20): thc stop signalled a service-managed bridge directly, so
// systemd kept the unit "active" while the bridge shut down, and a thc init in
// that window answered "Already initialized and running"; the bot then stayed
// stopped. Value: protects=thc stop asks systemd to stop a bridge that is the
// unit's main process, signals it directly otherwise or when systemd cannot be
// asked, signals it once, and waits only for the bridge it stopped;
// fails_when=a service bridge is signalled directly, a foreground bridge goes
// through systemctl, a failed systemctl leaves the bridge running, the bridge
// gets a second SIGTERM, or a newly started bridge keeps thc stop waiting;
// why_new=stop.test.ts never consults systemd; seam=none; no_claim=systemd is
// not run (exec is faked and sends the SIGTERM systemd would; QA probed a real
// unit), and init's reaction to "deactivating" is covered by
// service-unit.regression-3.test.ts

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

interface Bridge {
  pid: number;
  terms: () => number;
}
/**
 * A bridge stand-in that holds the folder lock. The first SIGTERM releases the
 * lock and exits 100 ms later; each SIGTERM is counted.
 */
async function bridge(root: string): Promise<Bridge> {
  const module = pathToFileURL(join(process.cwd(), "src/core/lock.ts")).href;
  const code = `import { acquireDirectoryLock } from ${JSON.stringify(module)}; const lease = await acquireDirectoryLock(process.argv[1]); let terms = 0; process.on('SIGTERM', () => { terms += 1; console.log('TERM'); if (terms === 1) lease.release().then(() => setTimeout(() => process.exit(0), 100)); }); console.log('READY'); setInterval(() => {}, 1000);`;
  const child = spawn(
    process.execPath,
    ["--input-type=module", "-e", code, root],
    { stdio: ["pipe", "pipe", "pipe"] },
  );
  children.push(child);
  let output = "";
  child.stdout.on("data", (chunk: Buffer) => {
    output += chunk.toString();
  });
  await new Promise<void>((resolve, reject) => {
    child.once("error", reject);
    child.once("exit", (code) => {
      reject(new Error(`child exited ${String(code)}`));
    });
    child.stdout.on("data", () => {
      if (output.includes("READY")) resolve();
    });
  });
  return {
    pid: child.pid ?? 0,
    terms: () => output.split("\n").filter((line) => line === "TERM").length,
  };
}

interface Systemd {
  deps: CliDependencies;
  calls: string[];
  timeouts: (number | undefined)[];
  output: () => string;
}
/**
 * systemctl as thc stop sees it. The unit reports `state` and `mainPid`; a
 * `stop --no-block` succeeds and signals the bridge the way systemd would,
 * unless `stopFails` is set.
 */
function systemd(
  state: string,
  mainPid: number | "fails",
  stopFails = false,
): Systemd {
  const calls: string[] = [];
  const timeouts: (number | undefined)[] = [];
  let output = "";
  const exec: CliDependencies["exec"] = (file, args, options) => {
    calls.push([file, ...args].join(" "));
    timeouts.push(options?.timeout);
    if (args.includes("show")) {
      if (mainPid === "fails")
        return Promise.reject(new Error("systemd user bus unavailable"));
      return Promise.resolve({
        stdout: `MainPID=${String(mainPid)}\nActiveState=${state}\n`,
        stderr: "",
      });
    }
    if (stopFails || typeof mainPid !== "number")
      return Promise.reject(new Error("Failed to stop unit"));
    process.kill(mainPid, "SIGTERM");
    return Promise.resolve({ stdout: "", stderr: "" });
  };
  const deps: CliDependencies = {
    ...defaults(),
    env: {},
    exec,
    stdout: (value) => {
      output += value;
    },
  };
  return { deps, calls, timeouts, output: () => output };
}
async function folder(): Promise<string> {
  const root = await mkdtemp(join(tmpdir(), "thc-stop-service-"));
  roots.push(root);
  return root;
}
function dead(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return false;
  } catch {
    return true;
  }
}

it("stops the service through systemd when the bridge is its main process", async () => {
  const root = await folder();
  const running = await bridge(root);
  const unit = systemd("active", running.pid);
  await stop(root, unit.deps);
  expect(unit.output()).toBe("Stop requested.\nStopped.\n");
  expect(unit.calls).toEqual([
    expect.stringMatching(
      /^systemctl --user show thc-[0-9a-f]{24}\.service --property ActiveState --property MainPID$/,
    ),
    expect.stringMatching(
      /^systemctl --user stop --no-block thc-[0-9a-f]{24}\.service$/,
    ),
  ]);
  // Both calls are bounded, so a stalled systemctl cannot hold thc stop.
  expect(unit.timeouts.every((timeout) => (timeout ?? 0) > 0)).toBe(true);
  expect(running.terms()).toBe(1);
  expect(dead(running.pid)).toBe(true);
});

it("signals a bridge that the service does not run", async () => {
  // A foreground thc start: the unit runs another process or none at all.
  for (const [state, mainPid] of [
    ["active", 1],
    ["inactive", 0],
  ] as const) {
    const root = await folder();
    const running = await bridge(root);
    const unit = systemd(state, mainPid);
    await stop(root, unit.deps);
    expect(unit.output()).toBe("Stop requested.\nStopped.\n");
    expect(unit.calls.some((call) => call.includes(" stop "))).toBe(false);
    expect(running.terms()).toBe(1);
  }
});

it("signals the bridge itself when systemd cannot be asked", async () => {
  for (const unit of [
    () => systemd("active", "fails"),
    (pid: number) => systemd("active", pid, true),
  ]) {
    const root = await folder();
    const running = await bridge(root);
    const service = unit(running.pid);
    await stop(root, service.deps);
    expect(service.output()).toBe("Stop requested.\nStopped.\n");
    expect(running.terms()).toBe(1);
    expect(dead(running.pid)).toBe(true);
  }
});

it("does not wait for a bridge that started after the stopped one exited", async () => {
  const root = await folder();
  const running = await bridge(root);
  const unit = systemd("active", running.pid);
  let next: Bridge | undefined;
  let clock = 0;
  unit.deps.now = () => (clock += 1000);
  // The restarted service takes the folder lock as soon as the old bridge is
  // gone, before thc stop looks again.
  unit.deps.sleep = async (ms) => {
    await new Promise((resolve) => setTimeout(resolve, ms));
    if (!next && dead(running.pid)) next = await bridge(root);
  };
  await stop(root, unit.deps);
  expect(next).toBeDefined();
  expect(unit.output()).toBe("Stop requested.\nStopped.\n");
  expect(next?.terms()).toBe(0);
});

it("asks systemd only about an active unit whose main process is the bridge", async () => {
  const cases: [string, number | "fails", boolean][] = [
    ["active", process.pid, true],
    ["deactivating", process.pid, false],
    ["inactive", process.pid, false],
    ["active", 0, false],
    ["active", process.pid + 1, false],
    ["active", "fails", false],
  ];
  for (const [state, mainPid, asked] of cases) {
    const unit = systemd(state, mainPid, true);
    expect(await requestServiceStop("/srv/bot", unit.deps, process.pid)).toBe(
      false,
    );
    expect(unit.calls.some((call) => call.includes(" stop "))).toBe(asked);
  }
});
