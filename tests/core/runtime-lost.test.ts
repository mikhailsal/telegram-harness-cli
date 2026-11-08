import { join } from "node:path";
import { expect, it } from "vitest";
import { RpcError } from "../../src/adapters/codex-protocol/transport.js";
import { startBridge } from "../../src/core/runtime.js";
import { openStore, type Store } from "../../src/store/index.js";
import {
  at,
  directory,
  event,
  config,
  FakeAdapter,
  FakeChannel,
  waitFor,
  inspect,
  channel,
} from "./runtime-fixture.js";

function seedWork(db: Store): void {
  db.createTurn({
    id: "done",
    entityId: "e",
    source: "telegram",
    createdAt: at,
  });
  db.transitionTurn("done", "queued", "active", {
    sessionId: "s",
    externalTurnId: "old-done",
  });
  db.transitionTurn("done", "active", "completed", { result: "old answer" });
  db.createTurn({
    id: "active",
    entityId: "e",
    source: "telegram",
    createdAt: at,
  });
  db.transitionTurn("active", "queued", "active", {
    sessionId: "s",
    externalTurnId: "old-active",
  });
  db.createRequest({
    id: "approval",
    turnId: "active",
    kind: "approval",
    payload: "{}",
    createdAt: at,
  });
  db.recordInbox({
    updateId: event.updateId,
    entityId: "e",
    chatId: event.chatId,
    userId: event.userId,
    receivedAt: at,
    payload: JSON.stringify(event),
  });
  db.queueInbox(
    { id: "queued", entityId: "e", source: "telegram", createdAt: at },
    [event.updateId],
  );
}
function seed(): void {
  const db = openStore(join(directory, ".thc", "store.db"));
  db.putEntity({ id: "e", directory, botId: "123456", createdAt: at });
  db.setCursor("entity-id", "e");
  db.activateSession(
    {
      id: "s",
      entityId: "e",
      harness: "protocol",
      externalId: "old",
      startedAt: at,
      endedAt: null,
    },
    JSON.stringify({ kind: "protocol", sessionId: "old" }),
  );
  seedWork(db);
  db.recordVisibleMessage({
    id: "old-visible",
    chatId: "7",
    telegramMessageId: 9,
    author: "assistant",
    sentAt: at,
    text: "old history",
    turnId: "done",
    possiblyDuplicated: false,
  });
  db.close();
}
class Missing extends FakeAdapter {
  override reconcile(): Promise<never[]> {
    return Promise.reject(
      new RpcError("no rollout found for thread id old", -32600),
    );
  }
}
it("interrupts lost work, cancels approval and continues queued input in a fresh thread", async () => {
  seed();
  const fake = new FakeChannel();
  const old = new Missing("old");
  const next = new FakeAdapter();
  const controller = new AbortController();
  const running = startBridge(directory, config(), {
    channel: channel(fake),
    adapterFactory: () => Promise.resolve(old),
    freshAdapterFactory: () => Promise.resolve(next),
    signal: controller.signal,
    tickIntervalMs: 20,
  });
  try {
    await waitFor(() => next.submitted === 1);
    expect(inspect((db) => db.getTurn("done")?.result)).toBe("old answer");
    expect(
      inspect((db) => db.searchMessages("old history", 10).messages),
    ).toHaveLength(1);
    expect(inspect((db) => db.getTurn("active")?.status)).toBe("interrupted");
    expect(inspect((db) => db.getRequest("approval")?.status)).toBe(
      "cancelled",
    );
    expect(inspect((db) => db.getSession("s")?.endedAt)).not.toBeNull();
    expect(fake.deliveries.some((text) => text.includes("interrupted"))).toBe(
      true,
    );
    expect(old.submitted).toBe(0);
  } finally {
    controller.abort();
    await running;
  }
});
it("does not rotate or interrupt when reconnection is ambiguous", async () => {
  seed();
  const fake = new FakeChannel();
  const controller = new AbortController();
  let replacement = 0;
  const logs: string[] = [];
  const running = startBridge(directory, config(), {
    channel: channel(fake),
    adapterFactory: () => Promise.reject(new Error("socket unavailable")),
    freshAdapterFactory: () => {
      replacement++;
      return Promise.resolve(new FakeAdapter());
    },
    signal: controller.signal,
    tickIntervalMs: 20,
    log: (name) => {
      logs.push(name);
    },
  });
  try {
    await waitFor(() => logs.includes("adapter.disconnected"));
    expect(replacement).toBe(0);
    expect(inspect((db) => db.getTurn("active")?.status)).toBe("active");
    expect(inspect((db) => db.getRequest("approval")?.status)).toBe("pending");
  } finally {
    controller.abort();
    await running;
  }
});
it("announces an unavailable daemon once and clears the notice on reconnection", async () => {
  const fake = new FakeChannel();
  const controller = new AbortController();
  let attempts = 0,
    starts = 0;
  const running = startBridge(directory, config(), {
    channel: channel(fake),
    adapterFactory: () => {
      attempts++;
      return attempts < 3
        ? Promise.reject(new Error("socket unavailable"))
        : Promise.resolve(new FakeAdapter());
    },
    startDaemon: () => {
      starts++;
      return Promise.resolve();
    },
    signal: controller.signal,
    tickIntervalMs: 20,
  });
  try {
    await waitFor(() => fake.deleted.includes(50), 6000);
    expect(attempts).toBe(3);
    expect(starts).toBe(0);
    expect(
      fake.notices.filter((text) => text.includes("not working")),
    ).toHaveLength(1);
  } finally {
    controller.abort();
    await running;
  }
});
it("waits for a recent uncertain submission despite an external turn ID", async () => {
  seed();
  const now = new Date();
  const db = openStore(join(directory, ".thc", "store.db"));
  db.createTurn({
    id: "new",
    entityId: "e",
    source: "telegram",
    createdAt: now.toISOString(),
  });
  db.transitionTurn("new", "queued", "submitting", {
    sessionId: "s",
    updatedAt: now.toISOString(),
  });
  db.transitionTurn("new", "submitting", "uncertain", {
    externalTurnId: "new-external",
    updatedAt: now.toISOString(),
  });
  db.close();
  const fake = new FakeChannel();
  const controller = new AbortController();
  let replacements = 0;
  const logs: string[] = [];
  const running = startBridge(directory, config(), {
    channel: channel(fake),
    adapterFactory: () => Promise.resolve(new Missing("old")),
    freshAdapterFactory: () => {
      replacements++;
      return Promise.resolve(new FakeAdapter());
    },
    signal: controller.signal,
    tickIntervalMs: 20,
    now: () => now,
    log: (name) => {
      logs.push(name);
    },
  });
  try {
    await waitFor(() => logs.includes("adapter.disconnected"));
    expect(replacements).toBe(0);
    expect(inspect((store) => store.getTurn("new")?.status)).toBe("uncertain");
    expect(inspect((store) => store.getTurn("active")?.status)).toBe("active");
  } finally {
    controller.abort();
    await running;
  }
});
it("rejects a missing-rollout error naming a different thread", async () => {
  seed();
  class Other extends FakeAdapter {
    override reconcile(): Promise<never[]> {
      return Promise.reject(
        new RpcError("no rollout found for thread id other", -32600),
      );
    }
  }
  const fake = new FakeChannel();
  const controller = new AbortController();
  let replacements = 0;
  const logs: string[] = [];
  const running = startBridge(directory, config(), {
    channel: channel(fake),
    adapterFactory: () => Promise.resolve(new Other("old")),
    freshAdapterFactory: () => {
      replacements++;
      return Promise.resolve(new FakeAdapter());
    },
    signal: controller.signal,
    tickIntervalMs: 20,
    log: (name) => {
      logs.push(name);
    },
  });
  try {
    await waitFor(() => logs.includes("adapter.disconnected"));
    expect(replacements).toBe(0);
    expect(inspect((store) => store.getTurn("active")?.status)).toBe("active");
  } finally {
    controller.abort();
    await running;
  }
});
function seedFailed(): void {
  const db = openStore(join(directory, ".thc", "store.db"));
  db.putEntity({ id: "e", directory, botId: "123456", createdAt: at });
  db.setCursor("entity-id", "e");
  db.activateSession(
    {
      id: "s",
      entityId: "e",
      harness: "protocol",
      externalId: "old",
      startedAt: at,
      endedAt: null,
    },
    JSON.stringify({ kind: "protocol", sessionId: "old" }),
  );
  db.createTurn({
    id: "failed",
    entityId: "e",
    source: "telegram",
    createdAt: at,
  });
  db.transitionTurn("failed", "queued", "active", {
    sessionId: "s",
    externalTurnId: "old-failed",
  });
  db.transitionTurn("failed", "active", "failed", { result: "old failure" });
  db.recordInbox({
    updateId: event.updateId,
    entityId: "e",
    chatId: event.chatId,
    userId: event.userId,
    receivedAt: at,
    payload: JSON.stringify(event),
  });
  db.queueInbox(
    { id: "queued", entityId: "e", source: "telegram", createdAt: at },
    [event.updateId],
  );
  db.close();
}
it("replaces a missing idle thread with only failed history and continues queued input", async () => {
  seedFailed();
  const fake = new FakeChannel();
  const old = new Missing("old");
  const next = new FakeAdapter();
  const controller = new AbortController();
  const running = startBridge(directory, config(), {
    channel: channel(fake),
    adapterFactory: () => Promise.resolve(old),
    freshAdapterFactory: () => Promise.resolve(next),
    signal: controller.signal,
    tickIntervalMs: 20,
  });
  try {
    await waitFor(() => next.submitted === 1);
    expect(inspect((db) => db.getTurn("failed")?.result)).toBe("old failure");
    expect(inspect((db) => db.getTurn("failed")?.status)).toBe("failed");
    expect(inspect((db) => db.getSession("s")?.endedAt)).not.toBeNull();
    expect(old.submitted).toBe(0);
  } finally {
    controller.abort();
    await running;
  }
});
