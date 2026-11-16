// Shared engine fixture for the continuation tests.
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, vi } from "vitest";
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

export type Deliver = (
  chat: string,
  chunk: { plain: string },
  options?: { replyToMessageId?: number },
) => Promise<{ messageIds: number[]; visibleText: string }>;

function seed(root: string) {
  const store = openStore(join(root, "store.sqlite"));
  const now = new Date("2026-11-15T07:40:00Z");
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
  return { store, now };
}
function telegram(deliverChunk: Deliver) {
  return {
    channel: {
      hasDraft: () => false,
      deliverChunk,
      answerCallback: vi.fn().mockResolvedValue(undefined),
      bot: {
        api: { sendMessage: vi.fn().mockResolvedValue({ message_id: 9 }) },
      },
    },
    transient: {
      removeKind: vi.fn().mockResolvedValue(undefined),
      sweep: vi.fn().mockResolvedValue(undefined),
      send: vi.fn().mockResolvedValue(1),
      create: vi.fn().mockResolvedValue(1),
      remove: vi.fn().mockResolvedValue(undefined),
      repost: vi.fn().mockResolvedValue(false),
      registerExisting: vi.fn(),
    },
  };
}
export function setup(overrides: Record<string, unknown> = {}) {
  const root = mkdtempSync(join(tmpdir(), "thc-continuation-"));
  roots.push(root);
  const { store, now } = seed(root);
  let nextMessageId = 100;
  const deliverChunk = vi.fn<Deliver>((_chat, chunk) =>
    Promise.resolve({
      messageIds: [nextMessageId++],
      visibleText: chunk.plain,
    }),
  );
  const context = {
    directory: root,
    config: parseConfig({
      token: { env: "TOKEN" },
      allowlist: [1],
      sandbox: "read-only",
      status: "off",
      ...overrides,
    }),
    store,
    adapter: {
      capabilities: new Set(["submit", "observe", "interrupt", "reconcile"]),
      submit: vi.fn().mockResolvedValue({ turnId: "turn-1" }),
      interrupt: vi.fn().mockResolvedValue(undefined),
    },
    entityId: "entity",
    sessionId: "session",
    adapterReady: true,
    now: () => now,
    ...telegram(deliverChunk),
    files: { download: vi.fn() },
    notice: {},
    signal: new AbortController().signal,
    log: vi.fn(),
    persistAdapter: vi.fn(),
    rotateSession: vi.fn(),
  } as unknown as RuntimeContext;
  const engine = new TurnEngine(context, new Delivery(context));
  return { store, engine, deliverChunk, context };
}
export async function recover(engine: TurnEngine) {
  await engine.event({
    kind: "turn-completed",
    sessionId: "thread",
    turnId: "turn-1",
    cursor: "turn-1/complete",
    data: { status: "interrupted" },
  });
  await engine.event({
    kind: "turn-started",
    sessionId: "thread",
    turnId: "recovery",
    cursor: "recovery/start",
    data: { rootTurnId: "turn-1" },
  });
  await engine.event({
    kind: "turn-completed",
    sessionId: "thread",
    turnId: "recovery",
    cursor: "recovery/complete",
    text: "Review done",
  });
}
export function message(updateId: number, text: string): ChannelEvent {
  return {
    kind: "message",
    updateId,
    text,
    attachments: [],
    chatId: "1",
    userId: "1",
    messageId: updateId,
    sentAt: "2026-11-15T07:36:34.000Z",
    language: "en",
  };
}
export function lastDelivery(
  deliverChunk: ReturnType<typeof setup>["deliverChunk"],
) {
  const call = deliverChunk.mock.calls.at(-1);
  return {
    plain: call?.[1].plain,
    options: call?.[2],
  };
}
