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

type Deliver = (
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
function setup() {
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
async function recover(engine: TurnEngine) {
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
function message(updateId: number, text: string): ChannelEvent {
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
function lastDelivery(deliverChunk: ReturnType<typeof setup>["deliverChunk"]) {
  const call = deliverChunk.mock.calls.at(-1);
  return {
    plain: call?.[1].plain,
    options: call?.[2],
  };
}

it("reports a Codex interruption truthfully and answers the continuation under the user's message", async () => {
  const { engine, store, deliverChunk } = setup();
  try {
    await engine.accept([message(7, "review my PR")], 8);
    await engine.event({
      kind: "turn-completed",
      sessionId: "thread",
      turnId: "turn-1",
      cursor: "turn-1/complete",
      data: { status: "interrupted" },
    });
    // protects=a harness-reported interruption names Codex, not the bridge; fails_when=the old "bridge restarted" text is used; no_claim=Telegram rendering (channel is a fake)
    expect(lastDelivery(deliverChunk).plain).toBe(
      "⚠️ Codex interrupted the task",
    );
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
    // protects=the recovery answer is a reply to the original message without the terminal marker; fails_when=it is shown as an unrelated 🖥 terminal task; no_claim=that Codex emits rootTurnId (adapter tests cover parsing)
    expect(lastDelivery(deliverChunk)).toEqual({
      plain: "Review done",
      options: expect.objectContaining({ replyToMessageId: 7 }) as unknown,
    });
    const recovery = store
      .listTurns(["completed"])
      .find((turn) => turn.externalTurnId === "recovery");
    expect(recovery?.source).toBe("terminal");
    expect(store.getCursor(`continues/${recovery?.id ?? ""}`)).toBeTruthy();
  } finally {
    await engine.close();
    store.close();
  }
});

it("keeps the bridge restart wording for turns the bridge lost", async () => {
  const { engine, store, deliverChunk } = setup();
  try {
    await engine.accept([message(7, "review my PR")], 8);
    const turn = store.listTurns(["active", "submitting", "uncertain"])[0];
    await engine.event({
      kind: "turn-completed",
      sessionId: "thread",
      cursor: `lost/${turn?.id ?? ""}`,
      correlationId: turn?.id ?? "",
      data: { status: "interrupted" },
    });
    // protects=the supervisor's own restart path still says the bridge restarted; fails_when=it uses the Codex wording; no_claim=supervisor detection of lost turns (event injected directly)
    expect(lastDelivery(deliverChunk).plain).toBe(
      "⚠️ Task interrupted when the bridge restarted",
    );
  } finally {
    await engine.close();
    store.close();
  }
});

it("leaves a turn whose root is not a Telegram turn as a terminal task", async () => {
  const { engine, store, deliverChunk } = setup();
  try {
    await engine.event({
      kind: "turn-started",
      sessionId: "thread",
      turnId: "local",
      cursor: "local/start",
      data: { rootTurnId: "unknown-root" },
    });
    await engine.event({
      kind: "turn-completed",
      sessionId: "thread",
      turnId: "local",
      cursor: "local/complete",
      text: "local answer",
    });
    // protects=only a matching Telegram root links a continuation; fails_when=an unrelated chain loses its 🖥 marker; no_claim=none beyond protects
    expect(lastDelivery(deliverChunk).plain).toBe("🖥 local answer");
    expect(
      store.getCursor(
        "continues/" + (store.listTurns(["completed"])[0]?.id ?? ""),
      ),
    ).toBeUndefined();
  } finally {
    await engine.close();
    store.close();
  }
});

it("answers the continuation under the latest accepted steer of the original turn", async () => {
  const { engine, store, deliverChunk, context } = setup();
  context.adapter.steer = vi.fn().mockResolvedValue(undefined);
  (context.adapter.capabilities as Set<string>).add("steer");
  store.setCursor("followup-mode", "steer");
  try {
    await engine.accept([message(7, "review my PR")], 8);
    await engine.accept([message(8, "also check tests")], 9);
    await recover(engine);
    // protects=a continuation inherits the reply rule of the turn it continues, including accepted steers; fails_when=it replies to the first message instead of the latest steer; no_claim=steer submission itself (adapter is a fake)
    expect(lastDelivery(deliverChunk).options).toEqual(
      expect.objectContaining({ replyToMessageId: 8 }),
    );
  } finally {
    await engine.close();
    store.close();
  }
});

it("delivers the continuation to the chat of the original turn", async () => {
  const { engine, store, deliverChunk } = setup();
  try {
    await engine.accept([message(7, "review my PR")], 8);
    store.setCursor("chat-id", "2");
    await recover(engine);
    // protects=a continuation goes to the chat that asked, not the most recent chat; fails_when=another allowlisted user's chat receives the answer; no_claim=multi-user admission (chat cursor set directly)
    expect(deliverChunk.mock.calls.at(-1)?.[0]).toBe("1");
  } finally {
    await engine.close();
    store.close();
  }
});
