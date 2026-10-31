import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, expect, it } from "vitest";
import type { HarnessAdapter } from "../../src/adapters/contract.js";
import { run, type CliDependencies } from "../../src/cli/main.js";
import { openStore } from "../../src/store/index.js";

let directory: string;
let output: string;
let errors: string;
let deps: Partial<CliDependencies>;
const at = "2026-10-31T10:00:00.000Z";
beforeEach(() => {
  directory = mkdtempSync(join(tmpdir(), "thc-commands-"));
  mkdirSync(join(directory, ".thc"));
  writeFileSync(join(directory, "token"), "123456:abcdefghijklmnop", {
    mode: 0o600,
  });
  writeFileSync(
    join(directory, ".thc", "config.json"),
    JSON.stringify({
      token: { file: join(directory, "token") },
      allowlist: [123],
      sandbox: "read-only",
    }),
  );
  const store = openStore(join(directory, ".thc", "store.db"));
  store.putEntity({ id: "e", directory, botId: "bot", createdAt: at });
  store.createTurn({
    id: "private-turn",
    entityId: "e",
    source: "telegram",
    createdAt: at,
  });
  store.recordVisibleMessage({
    id: "private-id",
    chatId: "c",
    telegramMessageId: 1,
    author: "user",
    sentAt: at,
    text: "deploy plan",
    turnId: "private-turn",
    possiblyDuplicated: false,
  });
  store.close();
  output = "";
  errors = "";
  deps = {
    cwd: directory,
    home: directory,
    env: {},
    isTTY: false,
    stdout: (value) => {
      output += value;
    },
    stderr: (value) => {
      errors += value;
    },
    exec: () => Promise.resolve({ stdout: "yes", stderr: "" }),
  };
});
afterEach(() => {
  rmSync(directory, { recursive: true, force: true });
});
function result(): unknown {
  return JSON.parse(output) as unknown;
}

it("reports stopped status, doctor checks and does not expose the token", async () => {
  expect(await run(["status", "--json"], deps)).toBe(0);
  expect(result()).toMatchObject({ running: false, failed: 0, pending: 0 });
  output = "";
  expect(await run(["doctor", "--json"], deps)).toBe(0);
  expect(result()).toMatchObject({
    token: "ok",
    codex: true,
    codexLogin: true,
  });
  expect(output).not.toContain("abcdefghijklmnop");
  output = "";
  expect(await run(["stop"], deps)).toBe(0);
  expect(output).toContain("not running");
});
it("tails a bounded log and renders only user-visible messages", async () => {
  writeFileSync(
    join(directory, ".thc", "bridge.log"),
    "first\nsecond\nthird\n",
  );
  expect(await run(["tail", "--lines", "2"], deps)).toBe(0);
  expect(output).toBe("second\nthird\n");
  output = "";
  expect(await run(["render", "--turn", "private-turn", "--json"], deps)).toBe(
    0,
  );
  expect(output).toContain("deploy plan");
  expect(output).not.toContain("private-id");
});
it("searches, navigates and explains errors without internal IDs", async () => {
  expect(await run(["search", "deploy"], deps)).toBe(0);
  expect(output).toContain("deploy plan");
  expect(output).not.toContain("private-id");
  output = "";
  expect(await run(["around", "2026-10-31 10:00:00", "2"], deps)).toBe(0);
  expect(output).toContain("deploy plan");
  output = "";
  expect(await run(["explain", "E_CONFIG"], deps)).toBe(0);
  expect(output).toContain("configuration");
});
it("maps invalid inputs to stable errors and JSON", async () => {
  expect(await run(["around", "yesterday", "--json"], deps)).toBe(2);
  expect(result()).toMatchObject({ error: { code: "E_TIME_FORMAT" } });
  output = "";
  expect(await run(["status", "--nonsense"], deps)).toBe(2);
  expect(errors).toContain("E_USAGE");
});
it("starts only through the supplied runtime entry point", async () => {
  let called = false;
  deps.startBridge = (path) => {
    called = path === directory;
    return Promise.resolve();
  };
  expect(await run(["start"], deps)).toBe(0);
  expect(called).toBe(true);
});
it("reconciles before attaching through the adapter", async () => {
  const events: string[] = [];
  const adapter: HarnessAdapter = {
    capabilities: new Set(["attach", "reconcile"]),
    newSession: () => Promise.resolve({ id: "unused" }),
    submit: () => Promise.resolve({}),
    observe: async function* () {
      await Promise.resolve();
      yield* [];
    },
    interrupt: () => Promise.resolve(),
    approve: () => Promise.resolve(),
    reconcile: () => {
      events.push("reconcile");
      return Promise.resolve([]);
    },
    attach: () => {
      events.push("attach");
      return Promise.resolve({ command: "tmux", args: ["attach"] });
    },
    close: () => {
      events.push("close");
      return Promise.resolve();
    },
  };
  deps.openAdapter = () => Promise.resolve(adapter);
  deps.exec = (file) => {
    events.push(file);
    return Promise.resolve({ stdout: "", stderr: "" });
  };
  expect(await run(["attach"], deps)).toBe(0);
  expect(events).toEqual(["reconcile", "attach", "tmux", "close"]);
});

it("interrupts only the identified active turn before attaching", async () => {
  const writable = openStore(join(directory, ".thc", "store.db"));
  writable.transitionTurn("private-turn", "queued", "active", {
    externalTurnId: "external-turn",
  });
  writable.close();
  const events: string[] = [];
  const adapter: HarnessAdapter = {
    capabilities: new Set(["attach", "reconcile", "interrupt"]),
    newSession: () => Promise.resolve({ id: "unused" }),
    submit: () => Promise.resolve({}),
    observe: async function* () {
      await Promise.resolve();
      yield* [];
    },
    approve: () => Promise.resolve(),
    reconcile: () => {
      events.push("reconcile");
      return Promise.resolve([]);
    },
    interrupt: (id) => {
      events.push(id);
      const db = openStore(join(directory, ".thc", "store.db"));
      db.transitionTurn("private-turn", "active", "interrupted");
      db.close();
      return Promise.resolve();
    },
    attach: () => Promise.resolve({ command: "tmux", args: ["attach"] }),
    close: () => Promise.resolve(),
  };
  deps.openAdapter = () => Promise.resolve(adapter);
  expect(await run(["attach", "--now"], deps)).toBe(0);
  expect(events).toEqual(["reconcile", "external-turn"]);
});

it("rejects attach interruption if the active turn has no verified external ID", async () => {
  const db = openStore(join(directory, ".thc", "store.db"));
  db.transitionTurn("private-turn", "queued", "submitting");
  db.close();
  const adapter: HarnessAdapter = {
    capabilities: new Set(["attach", "reconcile"]),
    newSession: () => Promise.resolve({ id: "x" }),
    submit: () => Promise.resolve({}),
    observe: async function* () {
      await Promise.resolve();
      yield* [];
    },
    interrupt: () => Promise.reject(new Error("must not interrupt")),
    approve: () => Promise.resolve(),
    reconcile: () => Promise.resolve([]),
    attach: () => Promise.resolve({ command: "tmux", args: [] }),
    close: () => Promise.resolve(),
  };
  deps.openAdapter = () => Promise.resolve(adapter);
  expect(await run(["attach", "--now", "--json"], deps)).toBe(4);
  expect(result()).toMatchObject({ error: { code: "E_DEPENDENCY" } });
});

it("redacts credentials in unexpected-error traces", async () => {
  deps.startBridge = () =>
    Promise.reject(
      new Error("failed https://api.telegram.org/bot123:secret-token/getMe"),
    );
  expect(await run(["start", "--json"], deps)).toBe(5);
  expect(output).toContain("E_INTERNAL");
  expect(output).not.toContain("secret-token");
  const trace = (result() as { error: { what: string } }).error.what.split(
    "trace: ",
  )[1];
  expect(trace).toBeDefined();
  const { readFileSync } = await import("node:fs");
  expect(readFileSync(trace ?? "", "utf8")).not.toContain("secret-token");
});

it("redacts Telegram credentials from diagnostic logs", async () => {
  writeFileSync(
    join(directory, ".thc", "bridge.log"),
    "network error https://api.telegram.org/bot123:secret-token/getUpdates\n",
  );
  expect(await run(["tail"], deps)).toBe(0);
  expect(output).not.toContain("secret-token");
  expect(output).toContain("<redacted>");
});

it("reports the actual process holding the folder lock", async () => {
  const { acquireDirectoryLock } = await import("../../src/core/lock.js");
  const lease = await acquireDirectoryLock(directory);
  try {
    expect(await run(["status", "--json"], deps)).toBe(0);
    expect(result()).toMatchObject({ running: true, pid: process.pid });
  } finally {
    await lease.release();
  }
});
