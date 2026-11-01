import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { openStore } from "../../src/store/index.js";
import type { Turn } from "../../src/store/index.js";
import { Delivery } from "../../src/core/delivery.js";
import { parseConfig } from "../../src/core/config.js";
import type { RuntimeContext } from "../../src/core/context.js";
import { replyTarget } from "../../src/core/reply-target.js";
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
function delivery() {
  return new Delivery({
    store,
    config: parseConfig({
      token: { env: "TOKEN" },
      allowlist: [1],
      sandbox: "read-only",
    }),
    now: () => new Date(at),
    log: vi.fn(),
  } as unknown as RuntimeContext);
}
it("leaves a simple final answer unquoted when its source is the latest chat message", () => {
  const source = turn("source");
  visible(source.id, 10);
  expect(delivery().finish(source, "completed", "answer")).toBe(true);
  expect(store.listOutbox(["pending"])[0]?.replyToMessageId).toBeNull();
});
it("quotes the source of queued answers after intervening user or assistant text", () => {
  const first = turn("first"),
    second = turn("second");
  visible(first.id, 10);
  visible(second.id, 11);
  expect(delivery().finish(first, "completed", "first answer")).toBe(true);
  expect(store.getOutbox(`${first.id}/0`)?.replyToMessageId).toBe(10);
  visible(first.id, 12, "assistant");
  expect(delivery().finish(second, "completed", "second answer")).toBe(true);
  expect(store.getOutbox(`${second.id}/0`)?.replyToMessageId).toBe(11);
});
it("targets the last accepted steer in the recipient chat without leaking another chat's message ID", () => {
  const parent = turn("parent", "shared");
  const steer = turn("steer", "shared");
  visible(parent.id, 10);
  visible(steer.id, 11);
  visible(steer.id, 99, "user", "other");
  visible(turn("later").id, 12);
  expect(replyTarget(store, parent, "1")).toBe(11);
});
it("suppresses an automatic quote only when the final message replaces its own live draft", async () => {
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
  expect(store.getOutbox(`${source.id}/0`)?.replyToMessageId).toBe(10);
  await sender.flush();
  expect(hasDraft).toHaveBeenCalledWith("1", turnDraftId(source), "rich");
  expect(deliverChunk.mock.calls[0]?.[2]).not.toHaveProperty(
    "replyToMessageId",
  );
  expect(deliverChunk.mock.calls[0]?.[2]).toHaveProperty(
    "fallbackReplyToMessageId",
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

it("preserves legacy queued reply targets when a matching draft exists", async () => {
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
    }),
    createdAt: at,
    replyToMessageId: 10,
  });
  await new Delivery(ctx).flush();
  expect(deliverChunk.mock.calls[0]?.[2]?.replyToMessageId).toBe(10);
});

it("rechecks the matching draft when a queued delivery retries", async () => {
  const source = turn("ddeeff-source");
  visible(source.id, 10);
  visible(turn("later").id, 11);
  let clock = new Date(at);
  let draft = true;
  const deliverChunk = vi
    .fn<TelegramChannel["deliverChunk"]>()
    .mockRejectedValueOnce(new Error("network"))
    .mockResolvedValueOnce({ messageIds: [12], visibleText: "answer" });
  const ctx = {
    store,
    channel: { hasDraft: () => draft, deliverChunk },
    config: parseConfig({
      token: { env: "TOKEN" },
      allowlist: [1],
      sandbox: "read-only",
    }),
    now: () => clock,
    log: vi.fn(),
  } as unknown as RuntimeContext;
  const sender = new Delivery(ctx);
  expect(sender.finish(source, "completed", "answer")).toBe(true);
  await sender.flush();
  expect(deliverChunk.mock.calls[0]?.[2]?.replyToMessageId).toBeUndefined();
  expect(store.getOutbox(`${source.id}/0`)?.status).toBe("pending");
  draft = false;
  clock = new Date(clock.getTime() + 120000);
  await sender.flush();
  expect(deliverChunk.mock.calls[1]?.[2]?.replyToMessageId).toBe(10);
  expect(store.getOutbox(`${source.id}/0`)?.status).toBe("sent");
});
