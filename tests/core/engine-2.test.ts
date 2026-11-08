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

function createPendingRequests(
  store: ReturnType<typeof openStore>,
  turnId: string,
  now: Date,
): void {
  for (const id of ["seen", "missing"])
    store.createRequest({
      id,
      turnId,
      kind: "approval",
      createdAt: now.toISOString(),
      payload: JSON.stringify({
        sessionId: "thread",
        requestId: id,
        chatId: "1",
        userId: "1",
        language: "en",
        externalTurnId: "turn-1",
        detail: "command",
        summary: "command",
        questions: [],
      }),
    });
}

it("allows replay grace, preserves verified requests, and interrupts unverifiable ones", async () => {
  const { engine, store, now, context, adapter } = setup();
  (context.adapter.capabilities as Set<string>).add("approve");
  try {
    await engine.accept([message(1, "task")], 2);
    const turn = store.listTurns(["active"])[0];
    if (!turn) throw new Error("missing turn");
    createPendingRequests(store, turn.id, now);
    await engine.recover([]);
    now.setTime(now.getTime() + 29000);
    await engine.tick();
    expect(store.getRequest("missing")?.status).toBe("pending");
    await engine.event({
      kind: "approval-requested",
      sessionId: "thread",
      turnId: "turn-1",
      requestId: "seen",
      cursor: "approval/seen",
      data: {},
    });
    now.setTime(now.getTime() + 2000);
    await engine.tick();
    expect(store.getRequest("seen")?.status).toBe("pending");
    expect(store.getRequest("missing")?.status).toBe("stale");
    expect(adapter.interrupt).toHaveBeenCalledWith("turn-1");
    await engine.event({
      kind: "approval-requested",
      sessionId: "thread",
      turnId: "turn-1",
      requestId: "missing",
      cursor: "approval/missing",
      data: {},
    });
    expect(store.listRequests(["pending"])).toHaveLength(1);
  } finally {
    await engine.close();
    store.close();
  }
});
it("records a request summary once without posting it to Telegram", async () => {
  const { engine, store, now, deliverChunk } = setup();
  try {
    await engine.accept([message(1, "task")], 2);
    const turn = store.listTurns(["active"])[0];
    if (!turn) throw new Error("missing turn");
    store.createRequest({
      id: "request-1",
      turnId: turn.id,
      kind: "approval",
      createdAt: now.toISOString(),
      payload: JSON.stringify({
        sessionId: "thread",
        requestId: "remote",
        chatId: "1",
        userId: "1",
        language: "en",
        externalTurnId: "turn-1",
        detail: "command",
        summary: "command",
        questions: [],
      }),
    });
    expect(store.answerRequest("request-1", "accept", now.toISOString())).toBe(
      true,
    );
    expect(store.resolveRequest("request-1", "answering", "answered")).toBe(
      true,
    );
    await engine.tick();
    await engine.tick();
    expect(deliverChunk).not.toHaveBeenCalled();
    expect(store.getTurn(turn.id)?.status).toBe("active");
    const summary = store
      .messagesForTurn(turn.id)
      .filter((row) => row.id === "request-summary/request-1");
    expect(summary).toHaveLength(1);
    expect(summary[0]?.text).toBe("You allowed: command · 12:00");
    expect(summary[0]?.author).toBe("system");
    await engine.event({
      kind: "turn-completed",
      sessionId: "thread",
      turnId: "turn-1",
      cursor: "final",
      text: "Done",
    });
    expect(deliverChunk).toHaveBeenCalledTimes(1);
  } finally {
    await engine.close();
    store.close();
  }
});
it("treats unrecognized slash commands as user input", async () => {
  const { engine, store, submit } = setup();
  try {
    await engine.accept([command(1, "research", "a topic")], 2);
    expect(submit.mock.calls[0]?.[0].text).toContain("/research a topic");
    expect(store.listTurns(["active"])).toHaveLength(1);
  } finally {
    await engine.close();
    store.close();
  }
});
it("fails visibly and interrupts an unsupported secret question", async () => {
  const { engine, store, context, adapter, deliverChunk } = setup();
  (context.adapter.capabilities as Set<string>).add("approve");
  try {
    await engine.accept([message(1, "task")], 2);
    await engine.event({
      kind: "question-requested",
      sessionId: "thread",
      turnId: "turn-1",
      requestId: "secret",
      cursor: "q",
      data: { questions: [{ id: "q", question: "secret", isSecret: true }] },
    });
    expect(adapter.interrupt).toHaveBeenCalledWith("turn-1");
    expect(store.listTurns(["failed"])).toHaveLength(1);
    expect(deliverChunk).toHaveBeenCalledTimes(1);
  } finally {
    await engine.close();
    store.close();
  }
});
