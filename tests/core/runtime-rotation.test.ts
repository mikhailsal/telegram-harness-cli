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

it("sends the welcome before queued work and removes it after the first delivery", async () => {
  const db = openStore(join(directory, ".thc", "store.db"));
  db.setCursor("welcome-pending", at);
  db.close();
  const fake = new FakeChannel();
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
    await waitFor(() => fake.deleted.includes(71));
    expect(fake.notices).toContain("Send something to check the connection.");
    expect(fake.deliveries).toEqual(["visible answer"]);
    expect(inspect((store) => store.getCursor("welcome-pending"))).toBe("");
  } finally {
    controller.abort();
    await running;
  }
});

it("rotates an idle session by persisting the replacement before closing the old one", async () => {
  const fake = new FakeChannel();
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
  const first = new FakeAdapter("thread-one");
  const second = new FakeAdapter("thread-two");
  const controller = new AbortController();
  const running = startBridge(directory, config(), {
    channel: channel(fake),
    adapterFactory: () => Promise.resolve(first),
    freshAdapterFactory: () => Promise.resolve(second),
    signal: controller.signal,
    tickIntervalMs: 20,
  });
  try {
    await waitFor(
      () =>
        inspect((db) => db.getCursor("adapter-descriptor"))?.includes(
          "thread-two",
        ) === true,
    );
    const sessions =
      inspect((db) => db.listSessions(db.getCursor("entity-id") ?? "")) ?? [];
    expect(sessions).toHaveLength(2);
    expect(
      sessions.find((session) => session.externalId === "thread-one")?.endedAt,
    ).toBeTruthy();
    expect(
      sessions.find((session) => session.externalId === "thread-two")?.endedAt,
    ).toBeNull();
    expect(first.closed).toBe(true);
    expect(second.closed).toBe(false);
  } finally {
    controller.abort();
    await running;
  }
});
it("refuses atomic session replacement while the previous turn is unresolved", () => {
  const db = openStore(join(directory, ".thc", "store.db"));
  try {
    db.putEntity({ id: "e", directory, botId: "1", createdAt: at });
    expect(
      db.activateSession(
        {
          id: "s1",
          entityId: "e",
          harness: "protocol",
          externalId: "old",
          startedAt: at,
          endedAt: null,
        },
        "old descriptor",
      ),
    ).toBe(true);
    db.createTurn({
      id: "t",
      entityId: "e",
      source: "telegram",
      createdAt: at,
    });
    db.transitionTurn("t", "queued", "submitting", { sessionId: "s1" });
    expect(
      db.activateSession(
        {
          id: "s2",
          entityId: "e",
          harness: "protocol",
          externalId: "new",
          startedAt: at,
          endedAt: null,
        },
        "new descriptor",
        "s1",
      ),
    ).toBe(false);
    expect(db.getCursor("active-session")).toBe("s1");
    expect(db.getCursor("adapter-descriptor")).toBe("old descriptor");
    expect(db.getSession("s2")).toBeUndefined();
  } finally {
    db.close();
  }
});

it("rejects a second process lock without sending a startup notice", async () => {
  const firstChannel = new FakeChannel();
  const first = new FakeAdapter();
  const controller = new AbortController();
  const running = startBridge(directory, config(), {
    channel: channel(firstChannel),
    adapterFactory: () => Promise.resolve(first),
    signal: controller.signal,
    tickIntervalMs: 20,
  });
  try {
    await waitFor(() => firstChannel.calls.length > 0);
    let sends = 0;
    const fetcher: typeof fetch = () => {
      sends++;
      return Promise.resolve(new Response("{}"));
    };
    await expect(
      startBridge(directory, config(), { fetch: fetcher }),
    ).rejects.toMatchObject({ code: "E_ALREADY_RUNNING" });
    expect(sends).toBe(0);
  } finally {
    controller.abort();
    await running;
  }
});
it("deduplicates invalid-config startup notices without exposing token", async () => {
  const { writeFileSync } = await import("node:fs");
  writeFileSync(
    join(directory, ".thc", "config.json"),
    JSON.stringify({
      token: { file: join(directory, "token") },
      allowlist: [7],
      sandbox: "invalid",
    }),
  );
  let sends = 0;
  const fetcher: typeof fetch = () => {
    sends++;
    return Promise.resolve(
      new Response(JSON.stringify({ ok: true, result: { message_id: 50 } }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
  };
  await expect(
    startBridge(directory, undefined, {
      fetch: fetcher,
      apiRoot: "https://example.invalid",
    }),
  ).rejects.toThrow();
  await expect(
    startBridge(directory, undefined, {
      fetch: fetcher,
      apiRoot: "https://example.invalid",
    }),
  ).rejects.toThrow();
  expect(sends).toBe(1);
});

it("does not create a replacement when an unresolved turn has lost session metadata", () => {
  const db = openStore(join(directory, ".thc", "store.db"));
  try {
    db.putEntity({ id: "e", directory, botId: "1", createdAt: at });
    db.createTurn({
      id: "t",
      entityId: "e",
      source: "telegram",
      createdAt: at,
    });
    db.transitionTurn("t", "queued", "submitting");
    expect(
      db.activateSession(
        {
          id: "new",
          entityId: "e",
          harness: "protocol",
          externalId: "replacement",
          startedAt: at,
          endedAt: null,
        },
        "new descriptor",
      ),
    ).toBe(false);
    expect(db.getCursor("adapter-descriptor")).toBeUndefined();
  } finally {
    db.close();
  }
});

it("requeues only a still-submitting definitively rejected steer", () => {
  const db = openStore(join(directory, ".thc", "store.db"));
  try {
    db.putEntity({ id: "e", directory, botId: "1", createdAt: at });
    db.putSession({
      id: "s",
      entityId: "e",
      harness: "protocol",
      externalId: "thread",
      startedAt: at,
      endedAt: null,
    });
    db.createTurn({
      id: "t",
      entityId: "e",
      source: "telegram",
      createdAt: at,
    });
    expect(
      db.transitionTurn("t", "queued", "submitting", {
        sessionId: "s",
        externalTurnId: "foreign",
      }),
    ).toBe(true);
    expect(db.requeueRejectedSteer("t", "2026-10-31T11:00:00Z")).toBe(true);
    expect(db.getTurn("t")).toMatchObject({
      status: "queued",
      sessionId: null,
      externalTurnId: null,
      updatedAt: "2026-10-31T11:00:00Z",
    });
    expect(db.requeueRejectedSteer("t", at)).toBe(false);
  } finally {
    db.close();
  }
});

it("backs up a prior schema before startup migrates it", async () => {
  const { DatabaseSync } = await import("node:sqlite");
  const { readdirSync } = await import("node:fs");
  const { migrate } = await import("../../src/store/schema.js");
  const db = new DatabaseSync(join(directory, ".thc", "store.db"));
  migrate(db, 1);
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
    await waitFor(() => fake.calls.length > 0);
    const saved = readdirSync(join(directory, ".thc")).find((name) =>
      name.startsWith("store.db.bak-"),
    );
    expect(saved).toBeDefined();
    const backup = new DatabaseSync(join(directory, ".thc", saved ?? ""), {
      readOnly: true,
    });
    try {
      expect(backup.prepare("PRAGMA user_version").get()?.user_version).toBe(1);
    } finally {
      backup.close();
    }
  } finally {
    controller.abort();
    await running;
  }
});

it("notifies an authentication outage without logging sensitive exception text", async () => {
  const fake = new FakeChannel();
  const controller = new AbortController();
  const events: string[] = [];
  const running = startBridge(directory, config(), {
    channel: channel(fake),
    signal: controller.signal,
    tickIntervalMs: 20,
    adapterFactory: () =>
      Promise.reject(new Error("Codex login required: sensitive-private-text")),
    log: (name, details) => {
      events.push(JSON.stringify({ name, details }));
    },
  });
  try {
    await waitFor(() => fake.notices.length === 1);
    expect(fake.notices[0]).toContain("Codex login is required");
    expect(events.join("\n")).not.toContain("sensitive-private-text");
  } finally {
    controller.abort();
    await running;
  }
});
