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
import { finishTmuxTurn, tmuxStream } from "./tmux-stream.fixture.ts";

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

it("binds the real-order tmux stream to its Telegram turn and starts the queued followup", async () => {
  const { root, store, context, engine, now } = setup();
  const fixture = await tmuxStream(root);
  context.adapter = fixture.adapter;
  store.putSession({
    id: "session",
    entityId: "entity",
    harness: "codex",
    externalId: fixture.session.id,
    startedAt: now.toISOString(),
    endedAt: null,
  });
  try {
    await engine.accept([message(1, "first")], 2);
    await engine.accept([message(2, "followup")], 3);
    const initial = await fixture.adapter.reconcile();
    expect(initial.map((event) => event.kind)).toEqual([
      "turn-started",
      "input",
    ]);
    for (const event of initial) await engine.event(event);
    await finishTmuxTurn(fixture.path);
    const after = await fixture.adapter.reconcile(initial.at(-1)?.cursor);
    for (const event of after) await engine.event(event);
    const completed = store.listTurns(["completed"]);
    expect(completed).toHaveLength(1);
    expect(completed[0]).toMatchObject({
      source: "telegram",
      result: "exact-marker",
    });
    expect(
      store
        .listTurns(["active", "submitting", "queued"])
        .every((turn) => turn.source === "telegram"),
    ).toBe(true);
    expect(fixture.paste).toHaveBeenCalledTimes(2);
  } finally {
    await engine.close();
    await fixture.adapter.close();
    store.close();
    vi.restoreAllMocks();
  }
});
