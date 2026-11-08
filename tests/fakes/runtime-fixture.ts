import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import { mkdtemp, mkdir, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { afterEach } from "vitest";
import { FakeServices } from "./servers.js";

const resources: {
  child?: ChildProcess;
  server: FakeServices;
  root: string;
}[] = [];
afterEach(async () => {
  for (const resource of resources.splice(0)) {
    if (
      resource.child?.exitCode === null &&
      resource.child.signalCode === null
    ) {
      resource.child.kill("SIGKILL");
      await exited(resource.child);
    }
    await resource.server.close();
    await rm(resource.root, { recursive: true, force: true });
  }
});
function exited(child: ChildProcess): Promise<void> {
  if (child.exitCode !== null || child.signalCode !== null)
    return Promise.resolve();
  return new Promise((resolveExit) => {
    child.once("exit", () => {
      resolveExit();
    });
  });
}
export async function waitUntil(
  check: () => boolean,
  timeout = 10000,
): Promise<void> {
  const deadline = Date.now() + timeout;
  while (!check()) {
    if (Date.now() > deadline) throw new Error("Timed out waiting for fixture");
    await new Promise((resolveWait) => setTimeout(resolveWait, 25));
  }
}
export async function fixture(botId = "123") {
  const root = await mkdtemp(join(tmpdir(), "thc-crash-"));
  await mkdir(join(root, ".thc"));
  const server = new FakeServices();
  await server.start();
  const resource: { child?: ChildProcess; server: FakeServices; root: string } =
    { server, root };
  resources.push(resource);
  function start(
    holdInbox = false,
    now?: string,
    status?: "message",
  ): ChildProcess {
    const child = spawn(
      process.execPath,
      [resolve("tests/fakes/bridge-child.ts")],
      {
        env: {
          ...process.env,
          THC_TEST_ROOT: root,
          THC_TEST_SERVER: server.url,
          THC_TEST_BOT_ID: botId,
          THC_TEST_HOLD_INBOX: holdInbox ? "1" : "0",
          ...(now ? { THC_TEST_NOW: now } : {}),
          ...(status ? { THC_TEST_STATUS: status } : {}),
        },
        stdio: ["ignore", "ignore", "pipe", "ipc"],
      },
    );
    resource.child = child;
    return child;
  }
  async function kill(): Promise<void> {
    const child = resource.child;
    if (!child) throw new Error("Missing child");
    child.kill("SIGKILL");
    await exited(child);
    await waitUntil(
      () =>
        spawnSync("flock", ["-n", join(root, ".thc", "lock"), "true"])
          .status === 0,
    );
    delete resource.child;
  }
  return { root, server, start, kill };
}
