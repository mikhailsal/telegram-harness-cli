import { SteerRejectedError } from "../../src/adapters/contract.js";
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

it("queues a follow-up when the server proves the active turn already ended", async () => {
  const { engine, store, context, submit } = setup();
  context.adapter.steer = vi.fn().mockRejectedValue(new SteerRejectedError());
  (context.adapter.capabilities as Set<string>).add("steer");
  store.setCursor("followup-mode", "steer");
  try {
    await engine.accept([message(1, "first"), message(2, "follow up")], 3);
    expect(store.listTurns(["queued"])).toHaveLength(1);
    expect(submit).toHaveBeenCalledTimes(1);
    await engine.event({
      kind: "turn-completed",
      sessionId: "thread",
      turnId: "turn-1",
      cursor: "done",
      text: "done",
    });
    expect(submit).toHaveBeenCalledTimes(2);
    expect(submit.mock.calls[1]?.[0].text).toContain("follow up");
  } finally {
    await engine.close();
    store.close();
  }
});
it("ignores an old adapter event queued across session rotation", async () => {
  const { engine, store, context, now, rotateSession } = setup();
  let proceed: (() => void) | undefined;
  rotateSession.mockImplementation(
    () =>
      new Promise<void>((resolve) => {
        proceed = () => {
          context.sessionId = "new-session";
          resolve();
        };
      }),
  );
  store.putSession({
    id: "new-session",
    entityId: "entity",
    harness: "codex",
    externalId: "new-thread",
    startedAt: now.toISOString(),
    endedAt: null,
  });
  try {
    const changing = engine.accept([command(1, "new")], 2);
    await vi.waitFor(() => {
      expect(proceed).toBeDefined();
    });
    const late = engine.event({
      kind: "turn-completed",
      sessionId: "thread",
      turnId: "old-turn",
      cursor: "old",
      text: "old response",
    });
    proceed?.();
    await changing;
    await late;
    expect(store.listTurns(["active", "completed"])).toHaveLength(0);
    expect(store.listOutbox(["pending", "sent"])).toHaveLength(0);
  } finally {
    await engine.close();
    store.close();
  }
});
it("shows reconnecting status for queued work while the adapter is offline", async () => {
  vi.useFakeTimers();
  const { engine, store, context, transient } = setup();
  context.config.status = "message";
  context.adapterReady = false;
  try {
    await engine.accept([message(1, "waiting")], 2);
    await vi.advanceTimersByTimeAsync(1200);
    expect(transient.create).toHaveBeenCalled();
  } finally {
    await engine.close();
    store.close();
    vi.useRealTimers();
  }
});
it("preserves a pending request replayed inside the recovery batch", async () => {
  const { engine, store, now, context, adapter } = setup();
  (context.adapter.capabilities as Set<string>).add("approve");
  try {
    await engine.accept([message(1, "task")], 2);
    const turn = store.listTurns(["active"])[0];
    if (!turn) throw new Error("missing turn");
    store.createRequest({
      id: "replayed",
      turnId: turn.id,
      kind: "approval",
      createdAt: now.toISOString(),
      payload: JSON.stringify({
        sessionId: "thread",
        requestId: "replayed",
        chatId: "1",
        userId: "1",
        language: "en",
        externalTurnId: "turn-1",
        detail: "command",
        summary: "command",
        questions: [],
      }),
    });
    await engine.recover([
      {
        kind: "approval-requested",
        sessionId: "thread",
        turnId: "turn-1",
        requestId: "replayed",
        cursor: "approval/replayed",
        data: {},
      },
    ]);
    now.setTime(now.getTime() + 31000);
    await engine.tick();
    expect(store.getRequest("replayed")?.status).toBe("pending");
    expect(adapter.interrupt).not.toHaveBeenCalled();
  } finally {
    await engine.close();
    store.close();
  }
});
