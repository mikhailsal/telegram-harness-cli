import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, expect, it } from "vitest";
import { ProtocolAdapter } from "../../src/adapters/codex-protocol/index.js";
import { CodexTmuxAdapter } from "../../src/adapters/codex-tmux/index.js";
import {
  createFreshAdapter,
  openAdapter,
  persistAdapter,
  probeAdapterSearch,
} from "../../src/core/adapter.js";
import { parseConfig } from "../../src/core/config.js";
import { openStore, type Store } from "../../src/store/index.js";

let directory: string;
let store: Store;
const at = "2026-10-31T10:00:00Z";
beforeEach(() => {
  directory = mkdtempSync(join(tmpdir(), "thc-factory-"));
  store = openStore(join(directory, "db"));
  store.putEntity({ id: "e", directory, botId: "1", createdAt: at });
});
afterEach(() => {
  store.close();
  rmSync(directory, { recursive: true, force: true });
});
function configuration(adapter: "protocol" | "tmux") {
  return parseConfig({
    token: { env: "TOKEN" },
    allowlist: [1],
    sandbox: "read-only",
    codex: {
      adapter,
      executable: process.execPath,
      socket: join(directory, "missing.sock"),
    },
  });
}
it("restores the exact protocol session and refuses an adapter mismatch", async () => {
  const empty = await openAdapter(directory, configuration("protocol"), store);
  expect(empty).toBeInstanceOf(ProtocolAdapter);
  expect(() => empty.attach()).toThrow(/No active session/);
  persistAdapter(empty, store, "thread-123");
  await empty.close();
  const restored = await openAdapter(
    directory,
    configuration("protocol"),
    store,
  );
  expect(await restored.attach()).toMatchObject({
    args: [
      "--remote",
      `unix://${join(directory, "missing.sock")}`,
      "resume",
      "thread-123",
    ],
  });
  await restored.close();
  await expect(
    openAdapter(directory, configuration("tmux"), store),
  ).rejects.toThrow(/differs/);
});
it("constructs a fresh adapter without clearing the persisted session", async () => {
  store.setCursor(
    "adapter-descriptor",
    JSON.stringify({ kind: "protocol", sessionId: "existing" }),
  );
  const fresh = await createFreshAdapter(
    directory,
    configuration("protocol"),
    store,
  );
  expect(() => fresh.attach()).toThrow(/No active session/);
  expect(store.getCursor("adapter-descriptor")).toContain("existing");
  await fresh.close();
});
it("constructs the tmux adapter without starting a tmux server", async () => {
  const fresh = await createFreshAdapter(
    directory,
    configuration("tmux"),
    store,
  );
  expect(fresh).toBeInstanceOf(CodexTmuxAdapter);
  await fresh.close();
  const restored = await openAdapter(directory, configuration("tmux"), store);
  expect(restored).toBeInstanceOf(CodexTmuxAdapter);
  await restored.close();
});
it("reports a failed search probe when the active protocol socket is unavailable", async () => {
  store.setCursor(
    "adapter-descriptor",
    JSON.stringify({ kind: "protocol", sessionId: "missing" }),
  );
  await expect(
    probeAdapterSearch(directory, configuration("protocol"), store),
  ).rejects.toThrow();
});

it("fails closed on malformed saved approval identity", async () => {
  store.createTurn({
    id: "t",
    entityId: "e",
    source: "telegram",
    createdAt: at,
  });
  store.createRequest({
    id: "r",
    turnId: "t",
    kind: "approval",
    payload: "{}",
    createdAt: at,
  });
  store.answerRequest("r", "accept");
  store.setCursor(
    "adapter-descriptor",
    JSON.stringify({ kind: "protocol", sessionId: "thread" }),
  );
  await expect(
    openAdapter(directory, configuration("protocol"), store),
  ).rejects.toThrow();
});
it("refuses to persist a protocol adapter with no session identity", async () => {
  const adapter = await createFreshAdapter(
    directory,
    configuration("protocol"),
    store,
  );
  expect(() => {
    persistAdapter(adapter, store);
  }).toThrow(/session id/i);
  expect(store.getCursor("adapter-descriptor")).toBeUndefined();
  await adapter.close();
});
it("rejects a tampered tmux server descriptor before launching a process", async () => {
  store.setCursor(
    "adapter-descriptor",
    JSON.stringify({
      kind: "tmux",
      descriptor: {
        server: "foreign",
        localId: "id",
        identity: {
          paneId: "%1",
          pid: "1",
          executable: process.execPath,
          startTime: "1",
        },
        cwd: directory,
        threadId: "",
        rolloutPath: "",
        cursor: 0,
        startedAt: 1,
        originator: "thc-00000000-0000-0000-0000-000000000000",
      },
    }),
  );
  await expect(
    openAdapter(directory, configuration("tmux"), store),
  ).rejects.toThrow(/server/i);
});
