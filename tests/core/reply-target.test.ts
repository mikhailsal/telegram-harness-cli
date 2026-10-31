import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { openStore } from "../../src/store/index.js";
import type { Turn } from "../../src/store/index.js";
import { Delivery } from "../../src/core/delivery.js";
import { parseConfig } from "../../src/core/config.js";
import type { RuntimeContext } from "../../src/core/context.js";
import { replyTarget } from "../../src/core/reply-target.js";

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
