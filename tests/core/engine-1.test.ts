import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, expect, it, vi } from "vitest";
import { openStore } from "../../src/store/index.js";
import { parseConfig } from "../../src/core/config.js";
import { Delivery } from "../../src/core/delivery.js";
import { TurnEngine } from "../../src/core/engine.js";
import type { RuntimeContext } from "../../src/core/context.js";
import { catalog } from "../../src/channels/index.js";
import type { ChannelEvent } from "../../src/channels/index.js";

const roots: string[] = [];
afterEach(() => {
  for (const root of roots.splice(0))
    rmSync(root, { recursive: true, force: true });
});
function fakeTransient() {
  return {
    removeKind: vi.fn().mockResolvedValue(undefined),
    sweep: vi.fn().mockResolvedValue(undefined),
    send: vi.fn().mockResolvedValue(1),
    create: vi.fn().mockResolvedValue(1),
    remove: vi.fn().mockResolvedValue(undefined),
    repost: vi.fn().mockResolvedValue(false),
    registerExisting: vi.fn(),
  };
}
function fakeServices() {
  const submit = vi
    .fn<
      (input: {
        correlationId: string;
        text: string;
      }) => Promise<{ turnId: string }>
    >()
    .mockResolvedValue({ turnId: "turn-1" });
  const adapter = {
    capabilities: new Set([
      "submit",
      "observe",
      "interrupt",
      "reconcile",
      "attach",
      "newSession",
    ]),
    submit,
    interrupt: vi.fn().mockResolvedValue(undefined),
    approve: vi.fn().mockResolvedValue(undefined),
  };
  let nextMessageId = 123;
  const deliverChunk = vi.fn((_chat: string, chunk: { plain: string }) =>
    Promise.resolve({
      messageIds: [nextMessageId++],
      visibleText: chunk.plain,
    }),
  );
  const download = vi.fn().mockResolvedValue("/file");
  const answerCallback = vi.fn().mockResolvedValue(undefined);
  const sendMessage = vi.fn().mockResolvedValue({ message_id: 88 });
  const transient = fakeTransient();
  const rotateSession = vi.fn().mockResolvedValue(undefined);
  return {
    adapter,
    submit,
    deliverChunk,
    download,
    transient,
    rotateSession,
    answerCallback,
    sendMessage,
  };
}
function seedStore(
  store: ReturnType<typeof openStore>,
  root: string,
  now: Date,
): void {
  store.putEntity({
    id: "entity",
    directory: root,
    botId: "bot",
    createdAt: now.toISOString(),
  });
  store.putSession({
    id: "session",
    entityId: "entity",
    harness: "codex",
    externalId: "thread",
    startedAt: now.toISOString(),
    endedAt: null,
  });
}
function fakeConfig() {
  return parseConfig({
    token: { env: "TOKEN" },
    allowlist: [1],
    sandbox: "read-only",
    status: "off",
  });
}
function setup() {
  const root = mkdtempSync(join(tmpdir(), "thc-engine-"));
  roots.push(root);
  const store = openStore(join(root, "store.sqlite"));
  const now = new Date("2026-10-31T12:00:00Z");
  seedStore(store, root, now);
  const {
    adapter,
    submit,
    deliverChunk,
    download,
    transient,
    rotateSession,
    answerCallback,
    sendMessage,
  } = fakeServices();
  const context = {
    directory: root,
    config: fakeConfig(),
    store,
    adapter,
    entityId: "entity",
    sessionId: "session",
    adapterReady: true,
    now: () => now,
    channel: { deliverChunk, answerCallback, bot: { api: { sendMessage } } },
    transient,
    files: { download },
    notice: {},
    signal: new AbortController().signal,
    log: vi.fn(),
    persistAdapter: vi.fn(),
    rotateSession,
  } as unknown as RuntimeContext;
  const delivery = new Delivery(context);
  const engine = new TurnEngine(context, delivery);
  return {
    root,
    store,
    now,
    adapter,
    submit,
    deliverChunk,
    download,
    transient,
    rotateSession,
    answerCallback,
    sendMessage,
    context,
    engine,
  };
}
function message(updateId: number, text: string): ChannelEvent {
  return {
    kind: "message",
    updateId,
    text,
    attachments: [],
    chatId: "1",
    userId: "1",
    messageId: updateId,
    sentAt: "2026-10-31T12:00:00.000Z",
    language: "en",
  };
}

function command(updateId: number, name: string, args = ""): ChannelEvent {
  return {
    kind: "command",
    command: name,
    arguments: args,
    updateId,
    messageId: updateId,
    chatId: "1",
    userId: "1",
    sentAt: "2026-10-31T12:00:00Z",
    language: "en",
  };
}

it("cancels queued work silently and interrupts only the active turn", async () => {
  const { engine, store, adapter, submit, deliverChunk } = setup();
  try {
    await engine.accept([message(1, "active"), message(2, "waiting")], 3);
    const command: ChannelEvent = {
      kind: "command",
      command: "stop",
      arguments: "",
      updateId: 3,
      messageId: 3,
      chatId: "1",
      userId: "1",
      sentAt: "2026-10-31T12:00:00Z",
      language: "en",
    };
    await engine.accept([command], 4);
    expect(adapter.interrupt).toHaveBeenCalledWith("turn-1");
    expect(store.listTurns(["cancelled"])).toHaveLength(1);
    expect(submit).toHaveBeenCalledTimes(1);
    expect(store.listOutbox(["pending", "sent"])).toHaveLength(0);
    await engine.event({
      kind: "turn-completed",
      sessionId: "thread",
      turnId: "turn-1",
      cursor: "stopped",
      data: { status: "aborted" },
    });
    expect(store.listTurns(["cancelled"])).toHaveLength(2);
    expect(deliverChunk).not.toHaveBeenCalled();
  } finally {
    await engine.close();
    store.close();
  }
});
it("retains a silent user stop across database reopen and a racing completion", async () => {
  const { root, engine, store, context, adapter, deliverChunk, submit } =
    setup();
  try {
    await engine.accept([message(1, "active")], 2);
    context.adapterReady = false;
    await engine.accept([command(2, "stop")], 3);
    expect(adapter.interrupt).not.toHaveBeenCalled();
  } finally {
    await engine.close();
    store.close();
  }
  context.store = openStore(join(root, "store.sqlite"));
  context.adapterReady = true;
  const resumed = new TurnEngine(context, new Delivery(context));
  try {
    await resumed.recover([
      {
        kind: "turn-completed",
        sessionId: "thread",
        turnId: "turn-1",
        cursor: "racing-completion",
        text: "must remain silent",
      },
    ]);
    expect(context.store.listTurns(["cancelled"])).toHaveLength(1);
    expect(context.store.listOutbox(["pending", "sent"])).toHaveLength(0);
    expect(deliverChunk).not.toHaveBeenCalled();
    expect(context.store.getCursor("stop-pending")).toBe("0");
    submit.mockResolvedValueOnce({ turnId: "turn-2" });
    await resumed.accept([message(3, "next task")], 4);
    expect(adapter.interrupt).not.toHaveBeenCalled();
    expect(submit).toHaveBeenCalledTimes(2);
  } finally {
    await resumed.close();
    context.store.close();
  }
});
it("steers a durable follow-up into the active turn only once", async () => {
  const { engine, store, context, submit } = setup();
  const steer = vi.fn().mockResolvedValue(undefined);
  context.adapter.steer = steer;
  (context.adapter.capabilities as Set<string>).add("steer");
  store.setCursor("followup-mode", "steer");
  try {
    await engine.accept([message(1, "first"), message(2, "follow up")], 3);
    expect(submit).toHaveBeenCalledTimes(1);
    expect(steer).toHaveBeenCalledTimes(1);
    expect(steer.mock.calls[0]?.[1]).toBe("turn-1");
    await engine.event({
      kind: "turn-completed",
      sessionId: "thread",
      turnId: "turn-1",
      cursor: "done",
      text: "done",
    });
    expect(store.listTurns(["active"])).toHaveLength(0);
    expect(store.listTurns(["completed"])).toHaveLength(2);
  } finally {
    await engine.close();
    store.close();
  }
});
it("records transient command replies and defers new sessions until active work ends", async () => {
  const { engine, store, rotateSession, transient, context } = setup();
  try {
    await engine.accept(
      [
        command(1, "start"),
        command(2, "mode", "queue"),
        command(3, "status"),
        command(4, "help"),
      ],
      5,
    );
    expect(transient.send).toHaveBeenCalledTimes(4);
    expect(store.getCursor("followup-mode")).toBe("queue");
    await engine.accept([message(5, "task"), command(6, "new")], 7);
    // A command later in the same poll observes the persisted queued turn before dispatch.
    expect(rotateSession).toHaveBeenCalledTimes(1);
    await engine.accept([command(7, "new")], 8);
    expect(store.getCursor("deferred-new")).toBe("1");
    await engine.event({
      kind: "turn-completed",
      sessionId: "thread",
      turnId: "turn-1",
      cursor: "done",
      text: "done",
    });
    expect(rotateSession).toHaveBeenCalledTimes(2);
    expect(rotateSession).toHaveBeenLastCalledWith(7);
    expect(transient.send).toHaveBeenCalledWith(
      expect.objectContaining({ kind: "command" }),
      catalog.en.newStarted,
    );
    const compact = vi.fn().mockResolvedValue(undefined);
    context.adapter.compact = compact;
    (context.adapter.capabilities as Set<string>).add("compact");
    await engine.accept([command(8, "compact")], 9);
    expect(compact).toHaveBeenCalledTimes(1);
  } finally {
    await engine.close();
    store.close();
  }
});
it("reports uncertain delivery after timeout without submitting again", async () => {
  const { engine, store, submit, now, deliverChunk } = setup();
  try {
    submit.mockRejectedValueOnce(new Error("unknown outcome"));
    await engine.accept([message(1, "once")], 2);
    now.setTime(now.getTime() + 31000);
    await engine.tick();
    expect(store.listTurns(["failed"])).toHaveLength(1);
    expect(deliverChunk).toHaveBeenCalledTimes(1);
    expect(submit).toHaveBeenCalledTimes(1);
  } finally {
    await engine.close();
    store.close();
  }
});

it("shows a cached update only for the current installed folder version", async () => {
  const { engine, store, root, transient, context } = setup();
  try {
    mkdirSync(join(root, ".thc"), { recursive: true });
    writeFileSync(join(root, ".thc", "version"), "1.2.3\n");
    writeFileSync(
      join(root, ".thc", "update-cache.json"),
      JSON.stringify({
        checkedAt: "2026-10-31T12:00:00Z",
        currentVersion: "1.2.3",
        latestVersion: "1.2.4",
        updateAvailable: true,
      }),
    );
    await engine.accept([command(90, "status")], 91);
    expect(transient.send).toHaveBeenLastCalledWith(
      expect.anything(),
      expect.stringContaining("npx telegram-harness-cli@latest init --update"),
    );
    context.config.update_check = false;
    await engine.accept([command(91, "status")], 92);
    expect(transient.send.mock.calls.at(-1)?.[1]).not.toContain(
      "init --update",
    );
    context.config.update_check = true;
    writeFileSync(join(root, ".thc", "version"), "0.9.0\n");
    await engine.accept([command(92, "status")], 93);
    expect(transient.send.mock.calls.at(-1)?.[1]).not.toContain(
      "init --update",
    );
  } finally {
    await engine.close();
    store.close();
  }
});
