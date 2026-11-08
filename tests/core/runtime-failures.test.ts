import { join } from "node:path";
import { expect, it } from "vitest";
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

it("retries a temporary polling failure without advancing the durable cursor", async () => {
  const fake = new FakeChannel();
  fake.pollFailures = 1;
  fake.updates = [event];
  const adapter = new FakeAdapter();
  const controller = new AbortController();
  const logs: string[] = [];
  const running = startBridge(directory, config(), {
    channel: channel(fake),
    adapterFactory: () => Promise.resolve(adapter),
    signal: controller.signal,
    tickIntervalMs: 20,
    log: (name) => {
      logs.push(name);
    },
  });
  try {
    await waitFor(() => fake.deliveries.length === 1);
    expect(fake.calls.slice(0, 2)).toEqual([0, 0]);
    expect(logs).toContain("telegram.poll-failed");
    expect(inspect((db) => db.getCursor("telegram-offset"))).toBe("11");
  } finally {
    controller.abort();
    await running;
  }
});
it("fails closed when saved adapter and active session identities disagree", async () => {
  const db = openStore(join(directory, ".thc", "store.db"));
  db.putEntity({ id: "e", directory, botId: "123456", createdAt: at });
  db.setCursor("entity-id", "e");
  db.activateSession(
    {
      id: "s",
      entityId: "e",
      harness: "protocol",
      externalId: "one",
      startedAt: at,
      endedAt: null,
    },
    JSON.stringify({ kind: "protocol", sessionId: "two" }),
  );
  db.close();
  const fake = new FakeChannel();
  fake.updates = [event];
  const adapter = new FakeAdapter();
  const controller = new AbortController();
  const logs: string[] = [];
  const running = startBridge(directory, config(), {
    channel: channel(fake),
    adapterFactory: () => Promise.resolve(adapter),
    signal: controller.signal,
    tickIntervalMs: 20,
    log: (name) => {
      logs.push(name);
    },
  });
  try {
    await waitFor(() => logs.includes("adapter.disconnected"));
    await waitFor(() => inspect((store) => store.getInbox(10)) !== undefined);
    expect(adapter.reconciled).toBe(0);
    expect(adapter.submitted).toBe(0);
    expect(inspect((store) => store.getCursor("active-session"))).toBe("s");
  } finally {
    controller.abort();
    await running;
  }
});
it("does not resend a welcome whose send intent survived without a message ID", async () => {
  const db = openStore(join(directory, ".thc", "store.db"));
  db.setCursor("welcome-pending", at);
  db.putTransient({
    id: `welcome/${at}`,
    kind: "welcome",
    chatId: "7",
    createdAt: at,
    expiresAt: null,
  });
  db.close();
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
      () => inspect((store) => store.getCursor("welcome-pending")) === "",
    );
    expect(fake.notices).toEqual([]);
  } finally {
    controller.abort();
    await running;
  }
});
it("keeps polling when Telegram menu registration fails", async () => {
  const fake = new FakeChannel();
  fake.updates = [event];
  fake.bot.api.setMyCommands = () =>
    Promise.reject(new Error("menu unavailable"));
  const adapter = new FakeAdapter();
  const controller = new AbortController();
  const logs: string[] = [];
  const running = startBridge(directory, config(), {
    channel: channel(fake),
    adapterFactory: () => Promise.resolve(adapter),
    signal: controller.signal,
    tickIntervalMs: 20,
    log: (name) => {
      logs.push(name);
    },
  });
  try {
    await waitFor(() => fake.deliveries.length === 1);
    expect(logs).toContain("telegram.menu-failed");
  } finally {
    controller.abort();
    await running;
  }
});

it("reconciles after a protocol disconnect notification before accepting new work", async () => {
  const fake = new FakeChannel();
  const first = new FakeAdapter();
  const restored = new FakeAdapter();
  const controller = new AbortController();
  let attempts = 0;
  const running = startBridge(directory, config(), {
    channel: channel(fake),
    adapterFactory: () => {
      attempts++;
      return Promise.resolve(attempts === 1 ? first : restored);
    },
    signal: controller.signal,
    tickIntervalMs: 20,
  });
  try {
    await waitFor(
      () => inspect((store) => store.getCursor("runtime-ready")) !== undefined,
    );
    first.events.push({
      kind: "error",
      sessionId: "thread",
      cursor: "connection/lost",
      text: "disconnected",
    });
    first.wake?.();
    await waitFor(() => restored.reconciled === 1);
    expect(first.closed).toBe(true);
    expect(attempts).toBe(2);
    expect(restored.submitted).toBe(0);
  } finally {
    controller.abort();
    await running;
  }
});
