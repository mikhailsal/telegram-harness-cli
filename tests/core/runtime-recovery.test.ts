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

it("never creates a new thread if unresolved work lacks the saved descriptor", async () => {
  const db = openStore(join(directory, ".thc", "store.db"));
  db.putEntity({ id: "e", directory, botId: "123456", createdAt: at });
  db.setCursor("entity-id", "e");
  db.createTurn({ id: "t", entityId: "e", source: "telegram", createdAt: at });
  db.transitionTurn("t", "queued", "submitting");
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
    expect(adapter.sessions).toBe(0);
    expect(adapter.submitted).toBe(0);
    expect(inspect((store) => store.getTurn("t")?.status)).toBe("submitting");
  } finally {
    controller.abort();
    await running;
  }
});
it("starts the shared daemon only after a connection failure when enabled", async () => {
  const fake = new FakeChannel();
  const controller = new AbortController();
  let starts = 0;
  const settings = config();
  settings.codex.daemon_autostart = true;
  const running = startBridge(directory, settings, {
    channel: channel(fake),
    signal: controller.signal,
    tickIntervalMs: 20,
    adapterFactory: () => Promise.reject(new Error("socket unavailable")),
    startDaemon: () => {
      starts++;
      return Promise.resolve();
    },
  });
  try {
    await waitFor(() => starts > 0);
    expect(starts).toBe(1);
  } finally {
    controller.abort();
    await running;
  }
});
it("retains the old durable session if creation of a replacement fails", async () => {
  const fake = new FakeChannel();
  const old = new FakeAdapter("old");
  const controller = new AbortController();
  let attempts = 0;
  fake.updates = [
    {
      kind: "command",
      command: "new",
      arguments: "",
      updateId: 11,
      chatId: "7",
      userId: "7",
      messageId: 21,
      sentAt: at,
      language: "en",
    },
  ];
  const running = startBridge(directory, config(), {
    channel: channel(fake),
    adapterFactory: () => Promise.resolve(old),
    freshAdapterFactory: () => {
      attempts++;
      return Promise.reject(new Error("unavailable"));
    },
    signal: controller.signal,
    tickIntervalMs: 20,
  });
  try {
    await waitFor(() => attempts > 0);
    expect(inspect((store) => store.getCursor("adapter-descriptor"))).toContain(
      "old",
    );
    expect(old.closed).toBe(false);
  } finally {
    controller.abort();
    await running;
  }
});
it("registers default and localized command menus without contacting an external service", async () => {
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
    await waitFor(() => fake.menus.length === 3);
    expect(fake.menus).toEqual([undefined, "en", "ru"]);
  } finally {
    controller.abort();
    await running;
  }
});

class SlowAdapter extends FakeAdapter {
  override submit(): Promise<{ turnId: string }> {
    this.submitted++;
    return Promise.resolve({ turnId: "slow" });
  }
}
const newCommand = {
  kind: "command" as const,
  command: "new",
  arguments: "",
  updateId: 11,
  chatId: "7",
  userId: "7",
  messageId: 21,
  sentAt: at,
  language: "en" as const,
};
it("defers a new session until the active turn has completed", async () => {
  const fake = new FakeChannel();
  fake.updates = [event, newCommand];
  const old = new SlowAdapter("old");
  const next = new FakeAdapter("new");
  const controller = new AbortController();
  let rotations = 0;
  const running = startBridge(directory, config(), {
    channel: channel(fake),
    adapterFactory: () => Promise.resolve(old),
    freshAdapterFactory: () => {
      rotations++;
      return Promise.resolve(next);
    },
    signal: controller.signal,
    tickIntervalMs: 20,
  });
  try {
    await waitFor(
      () => inspect((store) => store.getCursor("deferred-new")) === "1",
    );
    expect(rotations).toBe(0);
    old.events.push({
      kind: "turn-completed",
      sessionId: "old",
      turnId: "slow",
      cursor: "done",
      text: "done",
    });
    old.wake?.();
    await waitFor(
      () =>
        inspect((store) => store.getCursor("adapter-descriptor"))?.includes(
          "new",
        ) === true,
    );
    expect(rotations).toBe(1);
  } finally {
    controller.abort();
    await running;
  }
});
it("does not contact the update registry when the option is disabled", async () => {
  const fake = new FakeChannel();
  const adapter = new FakeAdapter();
  const controller = new AbortController();
  let calls = 0;
  const fetcher: typeof fetch = () => {
    calls++;
    return Promise.reject(new Error("must not fetch"));
  };
  const running = startBridge(directory, config(), {
    channel: channel(fake),
    adapterFactory: () => Promise.resolve(adapter),
    fetch: fetcher,
    signal: controller.signal,
    tickIntervalMs: 20,
  });
  try {
    await waitFor(() => fake.calls.length > 0);
    expect(calls).toBe(0);
  } finally {
    controller.abort();
    await running;
  }
});

it("refreshes enabled update metadata in the background without Telegram notifications", async () => {
  const fake = new FakeChannel();
  const adapter = new FakeAdapter();
  const controller = new AbortController();
  const settings = config();
  settings.update_check = true;
  const fetcher: typeof fetch = () =>
    Promise.resolve(
      new Response(JSON.stringify({ version: "99.0.0" }), { status: 200 }),
    );
  const running = startBridge(directory, settings, {
    channel: channel(fake),
    adapterFactory: () => Promise.resolve(adapter),
    fetch: fetcher,
    signal: controller.signal,
    tickIntervalMs: 20,
  });
  try {
    const { readUpdateCache } = await import("../../src/core/update.js");
    let found = false;
    const end = Date.now() + 5000;
    while (Date.now() < end && !found) {
      found = (await readUpdateCache(directory))?.latestVersion === "99.0.0";
      if (!found) await new Promise((resolve) => setTimeout(resolve, 20));
    }
    expect(found).toBe(true);
    expect(fake.notices).toEqual([]);
  } finally {
    controller.abort();
    await running;
  }
});
it("returns immediately for an already-aborted startup without taking the lock", async () => {
  const controller = new AbortController();
  controller.abort();
  const fake = new FakeChannel();
  await startBridge(directory, config(), {
    channel: channel(fake),
    signal: controller.signal,
  });
  expect(fake.calls).toHaveLength(0);
  const { readLockOwner } = await import("../../src/core/lock.js");
  expect(await readLockOwner(directory)).toBeUndefined();
});
it("refuses a store that belongs to a different bot", async () => {
  const db = openStore(join(directory, ".thc", "store.db"));
  db.putEntity({ id: "e", directory, botId: "different", createdAt: at });
  db.setCursor("entity-id", "e");
  db.close();
  const fake = new FakeChannel();
  await expect(
    startBridge(directory, config(), { channel: channel(fake) }),
  ).rejects.toThrow(/another folder or bot/);
  expect(fake.calls).toHaveLength(0);
});

it("replaces an unmaterialized protocol session only before any turn is bound", async () => {
  const { RpcError } =
    await import("../../src/adapters/codex-protocol/transport.js");
  class Unmaterialized extends FakeAdapter {
    override reconcile(): Promise<never[]> {
      return Promise.reject(
        new RpcError("no rollout found for thread id old", -32600),
      );
    }
  }
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
  db.close();
  const fake = new FakeChannel();
  const old = new Unmaterialized("old");
  const replacement = new FakeAdapter("replacement");
  const controller = new AbortController();
  const running = startBridge(directory, config(), {
    channel: channel(fake),
    adapterFactory: () => Promise.resolve(old),
    freshAdapterFactory: () => Promise.resolve(replacement),
    signal: controller.signal,
    tickIntervalMs: 20,
  });
  try {
    await waitFor(
      () =>
        inspect((store) => store.getCursor("adapter-descriptor"))?.includes(
          "replacement",
        ) === true,
    );
    expect(old.closed).toBe(true);
    expect(replacement.sessions).toBe(1);
    expect(replacement.submitted).toBe(0);
  } finally {
    controller.abort();
    await running;
  }
});
function seedBoundSession(): void {
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
  db.createTurn({ id: "t", entityId: "e", source: "telegram", createdAt: at });
  db.transitionTurn("t", "queued", "submitting", { sessionId: "s" });
  db.close();
}
it("never replaces a protocol session after a turn has been bound", async () => {
  const { RpcError } =
    await import("../../src/adapters/codex-protocol/transport.js");
  class Rejected extends FakeAdapter {
    override reconcile(): Promise<never[]> {
      return Promise.reject(new RpcError("thread unavailable", -32602));
    }
  }
  seedBoundSession();
  const fake = new FakeChannel();
  const old = new Rejected("old");
  const controller = new AbortController();
  let replacements = 0;
  const logs: string[] = [];
  const running = startBridge(directory, config(), {
    channel: channel(fake),
    adapterFactory: () => Promise.resolve(old),
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
    expect(inspect((store) => store.getCursor("adapter-descriptor"))).toContain(
      "old",
    );
    expect(inspect((store) => store.getTurn("t")?.status)).toBe("submitting");
  } finally {
    controller.abort();
    await running;
  }
});
