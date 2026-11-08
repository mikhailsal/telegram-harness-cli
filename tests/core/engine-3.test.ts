import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, expect, it, vi } from "vitest";
import { openStore } from "../../src/store/index.js";
import { parseConfig } from "../../src/core/config.js";
import { Delivery } from "../../src/core/delivery.js";
import { TurnEngine } from "../../src/core/engine.js";
import type { RuntimeContext } from "../../src/core/context.js";
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
  const editMessageText = vi.fn().mockResolvedValue(true);
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
    editMessageText,
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
function fakeChannel(services: ReturnType<typeof fakeServices>) {
  const { deliverChunk, answerCallback, sendMessage, editMessageText } =
    services;
  return {
    deliverChunk,
    answerCallback,
    bot: { api: { sendMessage, editMessageText } },
  };
}
function setup() {
  const root = mkdtempSync(join(tmpdir(), "thc-engine-"));
  roots.push(root);
  const store = openStore(join(root, "store.sqlite"));
  const now = new Date("2026-10-31T12:00:00Z");
  seedStore(store, root, now);
  const services = fakeServices();
  const { adapter, download, transient, rotateSession } = services;
  const context = {
    directory: root,
    config: fakeConfig(),
    store,
    adapter,
    entityId: "entity",
    sessionId: "session",
    adapterReady: true,
    now: () => now,
    channel: fakeChannel(services),
    transient,
    files: { download },
    notice: {},
    signal: new AbortController().signal,
    log: vi.fn(),
    persistAdapter: vi.fn(),
    rotateSession,
  } as unknown as RuntimeContext;
  const engine = new TurnEngine(context, new Delivery(context));
  return {
    root,
    store,
    now,
    ...services,
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

it("delivers mirrored terminal input through durable user-authored outbox", async () => {
  const { engine, store, context } = setup();
  context.config.mirror_non_tg_input = true;
  try {
    await engine.accept([message(1, "task")], 2);
    await engine.event({
      kind: "turn-completed",
      sessionId: "thread",
      turnId: "turn-1",
      cursor: "done",
      text: "done",
    });
    await engine.event({
      kind: "turn-started",
      sessionId: "thread",
      turnId: "terminal",
      cursor: "start",
    });
    await engine.event({
      kind: "input",
      sessionId: "thread",
      turnId: "terminal",
      cursor: "input",
      text: "terminal input",
    });
    const turn = store
      .listTurns(["active"])
      .find((item) => item.source === "terminal");
    if (!turn) throw new Error("missing terminal turn");
    const mirrored = store.messagesForTurn(turn.id);
    expect(mirrored).toHaveLength(1);
    expect(mirrored[0]?.author).toBe("user");
    expect(mirrored[0]?.text).toContain("terminal input");
    expect(
      store
        .listOutbox(["sent"])
        .some((row) => row.turnId === turn.id && row.chunk < 0),
    ).toBe(true);
  } finally {
    await engine.close();
    store.close();
  }
});
it("shows mode choices and binds callbacks to the saved card", async () => {
  const {
    engine,
    store,
    transient,
    sendMessage,
    answerCallback,
    editMessageText,
  } = setup();
  transient.create.mockImplementation(
    async (_intent: unknown, send: () => Promise<number>) => send(),
  );
  try {
    await engine.accept([command(1, "mode")], 2);
    expect(sendMessage).toHaveBeenCalledTimes(1);
    store.putTransient({
      id: "mode-card",
      kind: "command",
      chatId: "1",
      createdAt: "2026-10-31T12:00:00Z",
      expiresAt: null,
    });
    store.setTransientMessage("mode-card", 88, "2026-10-31T12:00:00Z");
    const callback: ChannelEvent = {
      kind: "decision",
      updateId: 2,
      messageId: 88,
      chatId: "1",
      userId: "1",
      callbackId: "cb",
      data: "mode:steer",
      sentAt: "2026-10-31T12:00:00Z",
      language: "en",
    };
    await engine.accept([callback], 3);
    expect(store.getCursor("followup-mode")).toBe("steer");
    expect(answerCallback).toHaveBeenCalledWith("cb");
    expect(transient.remove).not.toHaveBeenCalledWith("mode-card");
    expect(editMessageText).toHaveBeenCalledWith("1", 88, expect.any(String), {
      reply_markup: { inline_keyboard: [] },
    });
    await engine.accept(
      [{ ...callback, updateId: 3, messageId: 999, callbackId: "stale" }],
      4,
    );
    expect(answerCallback).toHaveBeenCalledWith("stale", expect.any(String));
  } finally {
    await engine.close();
    store.close();
  }
});
it("shows the unsupported compact response and preserves reactions for the next task", async () => {
  const { engine, store, transient, submit } = setup();
  try {
    store.recordVisibleMessage({
      id: "old-answer",
      chatId: "1",
      telegramMessageId: 2,
      author: "assistant",
      sentAt: "2026-10-31T11:59:00Z",
      text: "prior visible answer",
      turnId: null,
      possiblyDuplicated: false,
    });
    const reaction: ChannelEvent = {
      kind: "reaction",
      updateId: 2,
      messageId: 2,
      chatId: "1",
      userId: "1",
      sentAt: "2026-10-31T12:00:00Z",
      language: "en",
      reactions: ["👍"],
    };
    await engine.accept(
      [command(1, "compact"), reaction, message(3, "task")],
      4,
    );
    expect(transient.send).toHaveBeenCalled();
    expect(submit.mock.calls[0]?.[0].text).toContain("👍");
    expect(submit.mock.calls[0]?.[0].text).toContain("prior visible answer");
    expect(submit.mock.calls[0]?.[0].text).not.toContain("old-answer");
    expect(store.getCursor("next-reaction")).toBe("");
    expect(store.searchMessages("Reacted", 10).messages).toHaveLength(1);
  } finally {
    await engine.close();
    store.close();
  }
});
it("holds queued work until a secret-question interrupt is confirmed", async () => {
  const { engine, store, context, adapter, submit } = setup();
  (context.adapter.capabilities as Set<string>).add("approve");
  adapter.interrupt
    .mockRejectedValueOnce(new Error("offline"))
    .mockRejectedValueOnce(new Error("still offline"))
    .mockResolvedValue(undefined);
  try {
    await engine.accept([message(1, "first"), message(2, "second")], 3);
    await engine.event({
      kind: "question-requested",
      sessionId: "thread",
      turnId: "turn-1",
      requestId: "secret",
      cursor: "q2",
      data: { questions: [{ id: "q", question: "secret", isSecret: true }] },
    });
    expect(store.listTurns(["failed"])).toHaveLength(1);
    expect(submit).toHaveBeenCalledTimes(1);
    await engine.tick();
    expect(submit).toHaveBeenCalledTimes(2);
  } finally {
    await engine.close();
    store.close();
  }
});
it.each([
  ["completed", "already delivered"],
  ["cancelled", ""],
] as const)(
  "repairs a follow-up after its parent becomes %s before a crash",
  async (status, result) => {
    const { engine, store, context } = setup();
    const steer = vi.fn().mockResolvedValue(undefined);
    context.adapter.steer = steer;
    (context.adapter.capabilities as Set<string>).add("steer");
    store.setCursor("followup-mode", "steer");
    try {
      await engine.accept([message(1, "parent"), message(2, "followup")], 3);
      const active = store.listTurns(["active"]);
      expect(active).toHaveLength(2);
      const parent = active.find(
        (turn) => !store.getCursor(`followup-of/${turn.id}`),
      );
      const followup = active.find((turn) =>
        store.getCursor(`followup-of/${turn.id}`),
      );
      if (!parent || !followup) throw new Error("missing parent or followup");
      expect(
        store.transitionTurn(parent.id, "active", status, {
          result,
        }),
      ).toBe(true);
      store.setCursor("adapter-event/thread/replayed-completion", "1");
      await engine.recover([
        {
          kind: "turn-completed",
          sessionId: "thread",
          turnId: "turn-1",
          cursor: "replayed-completion",
          text: "already delivered",
        },
      ]);
      expect(store.getTurn(followup.id)?.status).toBe(status);
      expect(store.listTurns(["active"])).toHaveLength(0);
    } finally {
      await engine.close();
      store.close();
    }
  },
);
