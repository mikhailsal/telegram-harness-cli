import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, expect, it } from "vitest";
import { run, type CliDependencies } from "../../src/cli/main.js";
import { openStore } from "../../src/store/index.js";

let directory: string;
let output: string;
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
  deps = {
    cwd: directory,
    home: directory,
    env: {},
    isTTY: false,
    stdout: (value) => {
      output += value;
    },
    stderr: (value) => {
      output += value;
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

it("enforces the output budget and continues without exposing message IDs", async () => {
  const db = openStore(join(directory, ".thc", "store.db"));
  for (let i = 0; i < 4; i++)
    db.recordVisibleMessage({
      id: `sensitive-id-${String(i)}`,
      chatId: "c",
      telegramMessageId: 10 + i,
      author: "user",
      sentAt: at,
      text: `deployment ${"x".repeat(4000)}`,
      turnId: null,
      possiblyDuplicated: false,
    });
  db.close();
  expect(
    await run(["search", "deployment", "--max-chars", "800", "--json"], deps),
  ).toBe(0);
  expect(Array.from(output).length).toBeLessThanOrEqual(800);
  expect(output).not.toContain("sensitive-id");
  const parsed = result() as { nextBefore: string };
  expect(
    Buffer.from(parsed.nextBefore, "base64url").toString("utf8"),
  ).not.toContain("sensitive-id");
  output = "";
  expect(
    await run(
      [
        "search",
        "deployment",
        "--max-chars",
        "800",
        "--json",
        "--after",
        parsed.nextBefore,
      ],
      deps,
    ),
  ).toBe(0);
  expect(result()).toHaveProperty("entries");
});

it("reports live sandbox probe status without exposing the result payload", async () => {
  const { acquireDirectoryLock } = await import("../../src/core/lock.js");
  const lease = await acquireDirectoryLock(directory);
  deps.probeHistory = () => Promise.resolve({ exitCode: 0 });
  try {
    expect(await run(["doctor", "--json"], deps)).toBe(0);
    expect(result()).toMatchObject({ searchProbe: "ok" });
    deps.probeHistory = () => Promise.reject(new Error("unavailable"));
    output = "";
    expect(await run(["doctor", "--json"], deps)).toBe(0);
    expect(result()).toMatchObject({ searchProbe: "failed or unsupported" });
  } finally {
    await lease.release();
  }
});
it("classifies store, dependency and configuration errors", async () => {
  for (const [message, code] of [
    ["database is locked", "E_STORE_BUSY"],
    ["Unsupported database version 9", "E_STORE_SCHEMA_NEWER"],
    ["Database requires migration", "E_DEPENDENCY"],
  ]) {
    output = "";
    deps.startBridge = () => Promise.reject(new Error(message));
    expect(await run(["start", "--json"], deps)).toBe(4);
    expect(result()).toMatchObject({ error: { code } });
  }
  writeFileSync(join(directory, ".thc", "config.json"), "{broken");
  output = "";
  expect(await run(["doctor", "--json"], deps)).toBe(3);
  expect(result()).toMatchObject({ error: { code: "E_CONFIG" } });
});
it("rejects tampered continuation and reports empty logs", async () => {
  expect(await run(["tail"], deps)).toBe(0);
  expect(output).toContain("No log yet");
  output = "";
  expect(
    await run(["search", "deploy", "--after", "tampered", "--json"], deps),
  ).toBe(2);
  expect(result()).toMatchObject({ error: { code: "E_USAGE" } });
});

it("summarizes recent searches without retaining query text", async () => {
  const atNow = Date.parse("2026-10-31T12:00:00.000Z");
  deps.now = () => atNow;
  writeFileSync(
    join(directory, ".thc", "usage.jsonl"),
    [
      JSON.stringify({
        time: "2026-10-31T10:00:00.000Z",
        command: "search",
        words: 1,
        hits: 0,
        error: null,
      }),
      JSON.stringify({
        time: "2026-10-30T10:00:00.000Z",
        command: "search",
        words: 2,
        hits: 1,
        error: "E_TIME_FORMAT",
      }),
      JSON.stringify({
        time: "2026-09-01T10:00:00.000Z",
        command: "search",
        words: 3,
        hits: 0,
        error: null,
      }),
    ].join("\n"),
  );
  expect(await run(["doctor", "--json"], deps)).toBe(0);
  expect(result()).toMatchObject({ usage: { calls: 2, empty: 1, errors: 1 } });
});

it("continues around equal-second messages without exposing internal IDs", async () => {
  const db = openStore(join(directory, ".thc", "store.db"));
  for (let i = 0; i < 4; i++)
    db.recordVisibleMessage({
      id: `private-${String(i)}`,
      chatId: "c",
      telegramMessageId: 20 + i,
      author: "user",
      sentAt: at,
      text: `entry ${String(i)}`,
      turnId: null,
      possiblyDuplicated: false,
    });
  db.close();
  expect(
    await run(["around", "2026-10-31 10:00:00", "2", "--json"], deps),
  ).toBe(0);
  const first = result() as { entries: { text: string }[]; nextBefore: string };
  expect(first.entries).toHaveLength(2);
  expect(first.nextBefore).toBeTruthy();
  expect(output).not.toContain("private-");
  output = "";
  expect(
    await run(
      [
        "around",
        "2026-10-31 10:00:00",
        "2",
        "--json",
        "--after",
        first.nextBefore,
      ],
      deps,
    ),
  ).toBe(0);
  const next = result() as { entries: { text: string }[] };
  expect(next.entries).toHaveLength(2);
  expect(first.entries.map((item) => item.text)).not.toContain(
    next.entries[0]?.text,
  );
});

it("notifies startup configuration failures but preserves the original error", async () => {
  writeFileSync(join(directory, ".thc", "config.json"), "{invalid");
  const notified: string[] = [];
  deps.notifyStartupFailure = (path, error) => {
    notified.push(
      `${path}:${error instanceof SyntaxError ? "syntax" : "other"}`,
    );
    return Promise.reject(new Error("notice unavailable"));
  };
  expect(await run(["start", "--json"], deps)).toBe(3);
  expect(result()).toMatchObject({ error: { code: "E_CONFIG" } });
  expect(notified).toEqual([`${directory}:syntax`]);
});
it("does not send a startup notice for the existing folder owner", async () => {
  const { AlreadyRunningError } = await import("../../src/core/lock.js");
  let notified = false;
  deps.startBridge = () => Promise.reject(new AlreadyRunningError());
  deps.notifyStartupFailure = () => {
    notified = true;
    return Promise.resolve();
  };
  expect(await run(["start", "--json"], deps)).toBe(3);
  expect(result()).toMatchObject({ error: { code: "E_ALREADY_RUNNING" } });
  expect(notified).toBe(false);
});

it("shows visible neighbors with each search hit", async () => {
  const db = openStore(join(directory, ".thc", "store.db"));
  db.recordVisibleMessage({
    id: "later",
    chatId: "c",
    telegramMessageId: 9,
    author: "assistant",
    sentAt: "2026-10-31T10:01:00.000Z",
    text: "neighbor context",
    turnId: null,
    possiblyDuplicated: false,
  });
  db.close();
  expect(await run(["search", "deploy"], deps)).toBe(0);
  expect(output).toContain("deploy plan");
  expect(output).toContain("neighbor context");
});

it("shows a cached update only when update checks are enabled for the current version", async () => {
  writeFileSync(join(directory, ".thc", "version"), "1.0.0\n");
  writeFileSync(
    join(directory, ".thc", "update-cache.json"),
    JSON.stringify({
      checkedAt: "2026-10-31T00:00:00.000Z",
      currentVersion: "1.0.0",
      latestVersion: "2.0.0",
      updateAvailable: true,
    }),
  );
  expect(await run(["status", "--json"], deps)).toBe(0);
  expect(result()).toMatchObject({ updateAvailable: "2.0.0" });
  writeFileSync(
    join(directory, ".thc", "config.json"),
    JSON.stringify({
      token: { file: join(directory, "token") },
      allowlist: [123],
      sandbox: "read-only",
      update_check: false,
    }),
  );
  output = "";
  expect(await run(["status", "--json"], deps)).toBe(0);
  expect(result()).toMatchObject({ updateAvailable: null });
});
