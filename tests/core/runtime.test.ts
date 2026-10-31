import { join } from "node:path";
import { expect, it } from "vitest";
import type { HarnessAdapter } from "../../src/adapters/contract.js";
import { readLockOwner } from "../../src/core/lock.js";
import { startBridge } from "../../src/core/runtime.js";
import { openStore } from "../../src/store/index.js";
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

it("commits incoming updates before offset, deduplicates and delivers one visible response", async () => {
  const fake = new FakeChannel();
  fake.updates = [event, event];
  const adapter = new FakeAdapter();
  const controller = new AbortController();
  const running = startBridge(directory, config(), {
    channel: channel(fake),
    adapterFactory: () => Promise.resolve(adapter),
    signal: controller.signal,
    tickIntervalMs: 20,
  });
  try {
    await waitFor(() => inspect((db) => db.listOutbox(["sent"]).length) === 1);
    expect(adapter.submitted).toBe(1);
    expect(fake.deliveries).toEqual(["visible answer"]);
    expect(inspect((db) => db.getCursor("telegram-offset"))).toBe("11");
    expect(inspect((db) => db.getInbox(10)?.payload)).toContain("hello");
    expect(
      inspect((db) => db.searchMessages("visible", 10).messages),
    ).toHaveLength(1);
  } finally {
    controller.abort();
    await running;
  }
  expect(await readLockOwner(directory)).toBeUndefined();
});
it("continues accepting inbox while the adapter reconnects", async () => {
  const fake = new FakeChannel();
  fake.updates = [event];
  const adapter = new FakeAdapter();
  const controller = new AbortController();
  let attempts = 0;
  const running = startBridge(directory, config(), {
    channel: channel(fake),
    adapterFactory: () => {
      attempts++;
      return attempts === 1
        ? Promise.reject(new Error("offline"))
        : Promise.resolve(adapter);
    },
    signal: controller.signal,
    tickIntervalMs: 20,
  });
  try {
    await waitFor(() => inspect((db) => db.getInbox(10)) !== undefined);
    expect(adapter.submitted).toBe(0);
    await waitFor(() => fake.deliveries.length === 1);
    expect(attempts).toBeGreaterThan(1);
    expect(adapter.submitted).toBe(1);
  } finally {
    controller.abort();
    await running;
  }
});
it("notifies a polling conflict once and clears it after a healthy poll", async () => {
  const fake = new FakeChannel();
  fake.conflicts = 2;
  fake.updates = [event];
  const adapter = new FakeAdapter();
  const controller = new AbortController();
  const running = startBridge(directory, config(), {
    channel: channel(fake),
    adapterFactory: () => Promise.resolve(adapter),
    signal: controller.signal,
    tickIntervalMs: 20,
  });
  try {
    await waitFor(() => fake.deliveries.length === 1, 7000);
    await waitFor(() => fake.deleted.length === 1);
    expect(fake.notices).toHaveLength(1);
    expect(fake.notices[0]).toContain("Another bot instance");
    expect(fake.deleted).toEqual([50]);
    expect(inspect((db) => db.getCursor("poll-conflict"))).toBe("0");
  } finally {
    controller.abort();
    await running;
  }
});

function seedInFlight(): void {
  const db = openStore(join(directory, ".thc", "store.db"));
  db.putEntity({ id: "e", directory, botId: "123456", createdAt: at });
  db.setCursor("entity-id", "e");
  expect(
    db.activateSession(
      {
        id: "s",
        entityId: "e",
        harness: "protocol",
        externalId: "thread",
        startedAt: at,
        endedAt: null,
      },
      JSON.stringify({ kind: "protocol", sessionId: "thread" }),
    ),
  ).toBe(true);
  db.createTurn({ id: "t", entityId: "e", source: "terminal", createdAt: at });
  db.transitionTurn("t", "queued", "active", {
    sessionId: "s",
    externalTurnId: "turn",
  });
  db.finishTurnAndEnqueue(
    "t",
    "active",
    "completed",
    { result: "visible answer" },
    [
      {
        id: "o",
        turnId: "t",
        chunk: 0,
        chatId: "7",
        text: "visible answer",
        payload: JSON.stringify({
          kind: "message",
          text: "visible answer",
          plain: "visible answer",
        }),
        createdAt: at,
      },
    ],
  );
  db.beginSend("o");
  db.close();
}
it("recovers an in-flight delivery without resubmitting the agent turn", async () => {
  seedInFlight();
  const fake = new FakeChannel();
  const adapter = new FakeAdapter();
  const controller = new AbortController();
  const running = startBridge(directory, config(), {
    channel: channel(fake),
    adapterFactory: () => Promise.resolve(adapter),
    signal: controller.signal,
    tickIntervalMs: 20,
  });
  try {
    await waitFor(
      () => inspect((store) => store.getOutbox("o")?.status) === "sent",
    );
    expect(adapter.reconciled).toBe(1);
    expect(adapter.submitted).toBe(0);
    expect(fake.deliveries).toEqual(["visible answer"]);
    expect(inspect((store) => store.getOutbox("o")?.possiblyDuplicated)).toBe(
      true,
    );
    expect(
      inspect((store) => store.getCursor("first-delivery-at")),
    ).toBeTruthy();
  } finally {
    controller.abort();
    await running;
  }
});
it("releases the lock on abort while adapter creation is still pending", async () => {
  const fake = new FakeChannel();
  const controller = new AbortController();
  let finish: ((value: HarnessAdapter) => void) | undefined;
  const promise = new Promise<HarnessAdapter>((resolve) => {
    finish = resolve;
  });
  const running = startBridge(directory, config(), {
    channel: channel(fake),
    adapterFactory: () => promise,
    signal: controller.signal,
    tickIntervalMs: 20,
  });
  await waitFor(() => fake.calls.length > 0);
  controller.abort();
  await running;
  expect(await readLockOwner(directory)).toBeUndefined();
  const late = new FakeAdapter();
  finish?.(late);
  await waitFor(() => late.closed);
});
