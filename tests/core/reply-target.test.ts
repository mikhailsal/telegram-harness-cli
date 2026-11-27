import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { openStore } from "../../src/store/index.js";
import type { Turn } from "../../src/store/index.js";
import { Delivery } from "../../src/core/delivery.js";
import { parseConfig } from "../../src/core/config.js";
import type { RuntimeContext } from "../../src/core/context.js";
import { replySourceMessageId } from "../../src/core/reply-target.js";
import { turnDraftId } from "../../src/core/turn-draft.js";
import type { TelegramChannel } from "../../src/channels/index.js";

let store: ReturnType<typeof openStore>;
const at = "2026-10-31T12:00:00.000Z";
beforeEach(() => {
  store = openStore(":memory:");
  store.putEntity({ id: "e", directory: "/tmp", botId: "bot", createdAt: at });
  store.putSession({
    id: "s",
    entityId: "e",
    harness: "codex",
    externalId: "thread",
    startedAt: at,
    endedAt: null,
  });
});
afterEach(() => {
  store.close();
});
function turn(id: string, external = id): Turn {
  store.createTurn({ id, entityId: "e", source: "telegram", createdAt: at });
  store.transitionTurn(id, "queued", "active", {
    sessionId: "s",
    externalTurnId: external,
  });
  const row = store.getTurn(id);
  if (!row) throw new Error("Missing test turn");
  return row;
}
function visible(
  turnId: string,
  messageId: number,
  author: "user" | "assistant" = "user",
  chatId = "1",
) {
  store.recordVisibleMessage({
    id: `${author === "user" ? "inbound" : "outbound"}/${chatId}/${String(messageId)}`,
    chatId,
    telegramMessageId: messageId,
    author,
    sentAt: at,
    text: "visible text",
    turnId,
    possiblyDuplicated: false,
  });
}
function sender() {
  const deliverChunk = vi
    .fn<TelegramChannel["deliverChunk"]>()
    .mockResolvedValue({ messageIds: [50], visibleText: "answer" });
  const ctx = {
    store,
    channel: { hasDraft: () => false, deliverChunk },
    config: parseConfig({
      token: { env: "TOKEN" },
      allowlist: [1],
      sandbox: "read-only",
    }),
    now: () => new Date(at),
    log: vi.fn(),
  } as unknown as RuntimeContext;
  return { delivery: new Delivery(ctx), deliverChunk };
}
it("leaves a simple final answer unquoted when its source is the latest chat message", async () => {
  const source = turn("source");
  visible(source.id, 10);
  const { delivery: sending, deliverChunk } = sender();
  expect(sending.finish(source, "completed", "answer")).toBe(true);
  await sending.flush();
  expect(deliverChunk.mock.calls[0]?.[2]).not.toHaveProperty(
    "replyToMessageId",
  );
});
// Value: protects=an answer queued while its question was the latest message quotes it when another message arrives before it is sent; fails_when=the quote is decided only when the answer is queued; why_new=a retry wait let a newer question sit between them; seam=Delivery to deliverChunk; no_claim=how Telegram draws the quote
it("quotes the source when a message arrives while the answer waits to be sent", async () => {
  const source = turn("waiting-source");
  visible(source.id, 10);
  const { delivery: sending, deliverChunk } = sender();
  expect(sending.finish(source, "completed", "answer")).toBe(true);
  visible(turn("newer").id, 11);
  await sending.flush();
  expect(deliverChunk.mock.calls[0]?.[2]).toHaveProperty(
    "replyToMessageId",
    10,
  );
});
it("quotes the source of queued answers after intervening user or assistant text", async () => {
  const first = turn("first"),
    second = turn("second");
  visible(first.id, 10);
  visible(second.id, 11);
  const { delivery: sending, deliverChunk } = sender();
  deliverChunk.mockResolvedValueOnce({ messageIds: [12], visibleText: "a" });
  expect(sending.finish(first, "completed", "first answer")).toBe(true);
  await sending.flush();
  expect(deliverChunk.mock.calls[0]?.[2]?.replyToMessageId).toBe(10);
  expect(sending.finish(second, "completed", "second answer")).toBe(true);
  await sending.flush();
  expect(deliverChunk.mock.calls[1]?.[2]?.replyToMessageId).toBe(11);
});
it("targets the last accepted steer in the recipient chat without leaking another chat's message ID", () => {
  const parent = turn("parent", "shared");
  const steer = turn("steer", "shared");
  visible(parent.id, 10);
  visible(steer.id, 11);
  visible(steer.id, 99, "user", "other");
  visible(turn("later").id, 12);
  expect(replySourceMessageId(store, parent, "1")).toBe(11);
});
// Value: protects=a queued answer that replaces its own live preview still quotes its question when another message sits between them; fails_when=a matching draft drops the automatic quote again; why_new=the queue check found answers without their source; seam=Delivery to deliverChunk; no_claim=how Telegram draws the quote
it("quotes the source when the final message replaces its own live draft", async () => {
  const source = turn("aabbcc-source");
  visible(source.id, 10);
  visible(turn("later").id, 11);
  let draftPublished = false;
  const deliverChunk = vi
    .fn<TelegramChannel["deliverChunk"]>()
    .mockResolvedValue({ messageIds: [12], visibleText: "answer" });
  const hasDraft = vi.fn(
    (_chat: string, id: number) => draftPublished && id === turnDraftId(source),
  );
  const ctx = {
    store,
    channel: { hasDraft, deliverChunk },
    config: parseConfig({
      token: { env: "TOKEN" },
      allowlist: [1],
      sandbox: "read-only",
    }),
    now: () => new Date(at),
    log: vi.fn(),
  } as unknown as RuntimeContext;
  const sender = new Delivery(ctx);
  sender.onSending = () => {
    draftPublished = true;
    return Promise.resolve();
  };
  expect(sender.finish(source, "completed", "answer")).toBe(true);
  await sender.flush();
  expect(hasDraft).toHaveBeenCalledWith("1", turnDraftId(source), "rich");
  expect(deliverChunk.mock.calls[0]?.[2]).toHaveProperty(
    "replyToMessageId",
    10,
  );
  expect(deliverChunk.mock.calls[0]?.[1]).not.toHaveProperty("automaticReply");
});

it.each([
  {
    label: "different draft",
    draft: false,
    explicit: false,
    file: false,
    expected: 10,
  },
  {
    label: "explicit target",
    draft: true,
    explicit: true,
    file: false,
    expected: 42,
  },
  { label: "document", draft: true, explicit: false, file: true, expected: 10 },
])(
  "preserves the reply for $label",
  async ({ draft, explicit, file, expected }) => {
    const source = turn("bbccdd-source");
    visible(source.id, 10);
    visible(turn("later").id, 11);
    const deliverChunk = vi
      .fn<TelegramChannel["deliverChunk"]>()
      .mockResolvedValue({ messageIds: [12], visibleText: "answer" });
    const ctx = {
      store,
      channel: { hasDraft: () => draft, deliverChunk },
      config: parseConfig({
        token: { env: "TOKEN" },
        allowlist: [1],
        sandbox: "read-only",
      }),
      now: () => new Date(at),
      log: vi.fn(),
    } as unknown as RuntimeContext;
    const sender = new Delivery(ctx);
    expect(
      sender.finish(
        source,
        "completed",
        file ? "a".repeat(33000) : "answer",
        explicit ? { replyToMessageId: 42 } : {},
      ),
    ).toBe(true);
    await sender.flush();
    expect(deliverChunk.mock.calls[0]?.[2]?.replyToMessageId).toBe(expected);
    expect(deliverChunk.mock.calls[0]?.[1]).not.toHaveProperty(
      "automaticReply",
    );
  },
);

it("uses a rich carrier to replace the preview before converting a regular answer", async () => {
  const source = turn("regular-source");
  visible(source.id, 10);
  visible(turn("later").id, 11);
  store.setCursor("message-format/1", "regular");
  const deliverChunk = vi
    .fn<TelegramChannel["deliverChunk"]>()
    .mockResolvedValue({ messageIds: [12], visibleText: "answer" });
  const hasDraft = vi.fn(
    (_chat: string, _id: number, family?: string) => family === "rich",
  );
  const ctx = {
    store,
    channel: { hasDraft, deliverChunk },
    config: parseConfig({
      token: { env: "TOKEN" },
      allowlist: [1],
      sandbox: "read-only",
    }),
    now: () => new Date(at),
    log: vi.fn(),
  } as unknown as RuntimeContext;
  const sender = new Delivery(ctx);
  expect(sender.finish(source, "completed", "answer")).toBe(true);
  await sender.flush();
  expect(hasDraft).toHaveBeenCalledWith("1", turnDraftId(source), "rich");
  expect(deliverChunk.mock.calls[0]?.[2]).toMatchObject({
    consumeDraft: true,
    replyToMessageId: 10,
  });
  expect(deliverChunk.mock.calls[0]?.[1]).toMatchObject({ style: "html" });
});

// Value: protects=an answer queued by an older version, whose payload still carries automaticReply, parses and keeps its quote; fails_when=the chunk schema drops the legacy field or the flag suppresses the quote; why_new=the flag is no longer written; seam=outbox payload to deliverChunk; no_claim=rows of other versions
it("delivers a queued answer written by an older version with its quote", async () => {
  const source = turn("ccddee-source");
  const deliverChunk = vi
    .fn<TelegramChannel["deliverChunk"]>()
    .mockResolvedValue({ messageIds: [12], visibleText: "answer" });
  const ctx = {
    store,
    channel: { hasDraft: () => true, deliverChunk },
    config: parseConfig({
      token: { env: "TOKEN" },
      allowlist: [1],
      sandbox: "read-only",
    }),
    now: () => new Date(at),
    log: vi.fn(),
  } as unknown as RuntimeContext;
  store.enqueueOutbox({
    id: `${source.id}/0`,
    turnId: source.id,
    chunk: 0,
    chatId: "1",
    text: "answer",
    payload: JSON.stringify({
      kind: "message",
      text: "answer",
      plain: "answer",
      automaticReply: true,
    }),
    createdAt: at,
    replyToMessageId: 10,
  });
  await new Delivery(ctx).flush();
  expect(deliverChunk.mock.calls[0]?.[2]?.replyToMessageId).toBe(10);
  expect(deliverChunk.mock.calls[0]?.[1]).not.toHaveProperty("automaticReply");
});
// Value: protects=an answer an older version queued without a target, because its question was then the latest message, quotes it when a newer message arrives before the upgrade sends it; fails_when=a row without a stored target is never given an automatic quote; why_new=older versions stored no target for that case; seam=outbox row to deliverChunk; no_claim=rows of other versions
it("quotes the source of an older version's unquoted answer once a newer message arrives", async () => {
  const source = turn("ddeeff-source");
  visible(source.id, 10);
  visible(turn("newer").id, 11);
  store.enqueueOutbox({
    id: `${source.id}/0`,
    turnId: source.id,
    chunk: 0,
    chatId: "1",
    text: "answer",
    payload: JSON.stringify({
      kind: "message",
      text: "answer",
      plain: "answer",
    }),
    createdAt: at,
  });
  const { delivery: sending, deliverChunk } = sender();
  await sending.flush();
  expect(deliverChunk.mock.calls[0]?.[2]?.replyToMessageId).toBe(10);
});
