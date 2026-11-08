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
function fakeChannel(services: ReturnType<typeof fakeServices>) {
  return {
    hasDraft: () => false,
    deliverChunk: services.deliverChunk,
    answerCallback: services.answerCallback,
    bot: { api: { sendMessage: services.sendMessage } },
  };
}
function setup() {
  const root = mkdtempSync(join(tmpdir(), "thc-engine-"));
  roots.push(root);
  const store = openStore(join(root, "store.sqlite"));
  const now = new Date("2026-10-31T12:00:00Z");
  seedStore(store, root, now);
  const services = fakeServices();
  const {
    adapter,
    submit,
    deliverChunk,
    download,
    transient,
    rotateSession,
    answerCallback,
    sendMessage,
  } = services;
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

it("persists inbox and FIFO, correlates a final answer and then submits the next turn", async () => {
  const { engine, store, submit, deliverChunk } = setup();
  try {
    await engine.accept([message(1, "first"), message(2, "second")], 3);
    expect(store.getCursor("telegram-offset")).toBe("3");
    expect(submit).toHaveBeenCalledTimes(1);
    const first = store.listTurns(["active"])[0];
    expect(first).toBeDefined();
    expect(submit.mock.calls[0]?.[0].text).toContain("first");
    await engine.event({
      kind: "output",
      sessionId: "thread",
      turnId: "turn-1",
      cursor: "output",
      text: "answer",
    });
    submit.mockResolvedValueOnce({ turnId: "turn-2" });
    await engine.event({
      kind: "turn-completed",
      sessionId: "thread",
      turnId: "turn-1",
      cursor: "done",
      data: { status: "completed" },
    });
    expect(deliverChunk).toHaveBeenCalledTimes(1);
    await engine.event({
      kind: "turn-completed",
      sessionId: "thread",
      turnId: "turn-1",
      cursor: "done",
      text: "duplicate",
    });
    expect(deliverChunk).toHaveBeenCalledTimes(1);
    expect(store.getTurn(first?.id ?? "")?.result).toBe("answer");
    expect(submit).toHaveBeenCalledTimes(2);
    expect(submit.mock.calls[1]?.[0].text).toContain("second");
  } finally {
    await engine.close();
    store.close();
  }
});
it("never blindly resubmits an uncertain operation after recovery", async () => {
  const { engine, store, submit } = setup();
  try {
    submit.mockRejectedValueOnce(new Error("connection lost"));
    await engine.accept([message(1, "once")], 2);
    expect(store.listTurns(["uncertain"])).toHaveLength(1);
    await engine.recover([]);
    await engine.tick();
    expect(submit).toHaveBeenCalledTimes(1);
  } finally {
    await engine.close();
    store.close();
  }
});
it("records updates during disconnection and dispatches only after readiness", async () => {
  const { engine, store, submit, context } = setup();
  try {
    context.adapterReady = false;
    await engine.accept([message(9, "offline")], 10);
    expect(store.getCursor("telegram-offset")).toBe("10");
    expect(store.listTurns(["queued"])).toHaveLength(1);
    expect(submit).not.toHaveBeenCalled();
    context.adapterReady = true;
    await engine.tick();
    expect(submit).toHaveBeenCalledTimes(1);
  } finally {
    await engine.close();
    store.close();
  }
});
it("groups an album after the quiet window and links downloaded attachments", async () => {
  const { engine, store, submit, now, download } = setup();
  const one = message(1, "caption");
  const two = message(2, "");
  if (one.kind !== "message" || two.kind !== "message")
    throw new Error("bad fixture");
  one.mediaGroupId = "album";
  two.mediaGroupId = "album";
  one.attachments = [{ kind: "document", fileId: "f1", fileUniqueId: "u1" }];
  two.attachments = [{ kind: "photo", fileId: "f2", fileUniqueId: "u2" }];
  try {
    await engine.accept([one, two], 3);
    expect(submit).not.toHaveBeenCalled();
    now.setTime(now.getTime() + 2100);
    await engine.tick();
    expect(submit).toHaveBeenCalledTimes(1);
    expect(download).toHaveBeenCalledTimes(2);
    expect(store.listTurns(["active"])).toHaveLength(1);
    expect(submit.mock.calls[0]?.[0].text).toContain("caption");
  } finally {
    await engine.close();
    store.close();
  }
});
it("delivers terminal-origin answers with the configured marker", async () => {
  const { engine, store, deliverChunk } = setup();
  try {
    await engine.accept([message(1, "bridge turn")], 2);
    await engine.event({
      kind: "turn-completed",
      sessionId: "thread",
      turnId: "turn-1",
      cursor: "first-done",
      text: "bridge done",
    });
    await engine.event({
      kind: "turn-started",
      sessionId: "thread",
      turnId: "terminal-turn",
      cursor: "terminal-start",
    });
    await engine.event({
      kind: "input",
      sessionId: "thread",
      turnId: "terminal-turn",
      cursor: "terminal-input",
      text: "local question",
    });
    await engine.event({
      kind: "turn-completed",
      sessionId: "thread",
      turnId: "terminal-turn",
      cursor: "terminal-done",
      text: "local answer",
    });
    expect(
      store.listTurns(["completed"]).find((turn) => turn.source === "terminal")
        ?.result,
    ).toContain("local answer");
    expect(deliverChunk.mock.calls.at(-1)?.[1].plain).toContain("🖥");
  } finally {
    await engine.close();
    store.close();
  }
});
