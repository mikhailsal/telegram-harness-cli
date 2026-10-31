import { afterEach, expect, it, vi } from "vitest";
import { TelegramChannel } from "../../src/channels/index.js";
import { Delivery } from "../../src/core/delivery.js";
import { parseConfig } from "../../src/core/config.js";
import type { RuntimeContext } from "../../src/core/context.js";
import { openStore } from "../../src/store/index.js";
import type { Turn } from "../../src/store/index.js";

const stores: ReturnType<typeof openStore>[] = [];
afterEach(() => {
  for (const store of stores.splice(0)) store.close();
});
function transport() {
  const calls: { method: string; body: Record<string, unknown> }[] = [];
  const failures: { code: number; after?: number; description?: string }[] = [];
  const fetcher: typeof fetch = (input, init) => {
    const url =
      typeof input === "string"
        ? input
        : input instanceof URL
          ? input.href
          : input.url;
    const method = url.split("/").at(-1) ?? "";
    const body =
      typeof init?.body === "string"
        ? (JSON.parse(init.body) as Record<string, unknown>)
        : {};
    calls.push({ method, body });
    const failure = failures.shift();
    if (failure?.code)
      return Promise.resolve(
        Response.json({
          ok: false,
          error_code: failure.code,
          description: failure.description ?? "failure",
          parameters: { retry_after: failure.after },
        }),
      );
    return Promise.resolve(
      Response.json({
        ok: true,
        result: {
          message_id: calls.length,
          date: 1,
          chat: { id: 1, type: "private" },
        },
      }),
    );
  };
  return { calls, failures, fetcher };
}
function activeTurn(
  store: ReturnType<typeof openStore>,
  id: string,
  source: Turn["source"],
  createdAt: string,
): Turn {
  store.createTurn({ id, entityId: "e", source, createdAt });
  store.transitionTurn(id, "queued", "active");
  const value = store.getTurn(id);
  if (!value) throw new Error("missing turn");
  return value;
}
function setup() {
  const store = openStore(":memory:");
  stores.push(store);
  const { calls, failures, fetcher } = transport();
  let clock = new Date("2026-10-31T12:00:00.000Z");
  store.putEntity({
    id: "e",
    directory: "/tmp",
    botId: "bot",
    createdAt: clock.toISOString(),
  });
  const log = vi.fn();
  const channel = new TelegramChannel({
    token: "123:abc",
    allowedUserIds: [1],
    fetch: fetcher,
  });
  const ctx = {
    store,
    channel,
    config: parseConfig({
      token: { env: "TOKEN" },
      allowlist: [1],
      sandbox: "read-only",
      language: "en",
    }),
    now: () => clock,
    log,
  } as unknown as RuntimeContext;
  const delivery = new Delivery(ctx);
  return {
    store,
    calls,
    failures,
    delivery,
    turn: (id: string, source: Turn["source"] = "telegram") =>
      activeTurn(store, id, source, clock.toISOString()),
    log,
    advance: (ms: number) => {
      clock = new Date(clock.getTime() + ms);
    },
  };
}
it("atomically finalizes inbox and delivers ordered chunks with one reply and one sound", async () => {
  const t = setup(),
    turn = t.turn("turn");
  t.store.recordInbox({
    updateId: 1,
    entityId: "e",
    chatId: "1",
    userId: "1",
    receivedAt: turn.createdAt,
    payload: "{}",
  });
  t.store.assignInbox(1, turn.id);
  const onDelivered = vi.fn().mockResolvedValue(undefined);
  t.delivery.onDelivered = onDelivered;
  expect(
    t.delivery.finish(
      turn,
      "completed",
      `${"a".repeat(20000)}\n\n${"b".repeat(20000)}`,
      { replyToMessageId: 22 },
    ),
  ).toBe(true);
  expect(t.store.getInbox(1)?.status).toBe("consumed");
  await t.delivery.flush();
  expect(t.calls).toHaveLength(2);
  expect(t.calls[0]?.body.disable_notification).toBe(false);
  expect(t.calls[1]?.body.disable_notification).toBe(true);
  expect(t.calls[0]?.body.reply_parameters).toEqual({ message_id: 22 });
  expect(t.calls[1]?.body.reply_parameters).toBeUndefined();
  expect(t.store.messagesForTurn(turn.id)).toHaveLength(2);
  expect(onDelivered).toHaveBeenCalledOnce();
  expect(t.store.getCursor("first-delivery-at")).toBe(
    "2026-10-31T12:00:00.000Z",
  );
  expect(t.delivery.finish(turn, "completed", "again")).toBe(false);
});
it("never persists or sends hidden text, warns for unclosed markup, and renders empty non-Telegram answers", async () => {
  const t = setup(),
    turn = t.turn("hidden", "terminal");
  t.delivery.finish(turn, "completed", "🖥 <internal-note>secret");
  expect(t.store.getTurn(turn.id)?.result).toBe("🖥 ✓ Done, no text response");
  expect(t.store.getCursor("markup-warnings")).toBe("1");
  expect(JSON.stringify(t.store.listOutbox(["pending"]))).not.toContain(
    "secret",
  );
  await t.delivery.flush();
  expect(JSON.stringify(t.calls)).not.toContain("secret");
  expect(JSON.stringify(t.store.messagesForTurn(turn.id))).not.toContain(
    "secret",
  );
  expect(JSON.stringify(t.log.mock.calls)).not.toContain("secret");
});
it("persists interim user and system messages without firing final delivery hooks", async () => {
  const t = setup(),
    turn = t.turn("interim");
  const onDelivered = vi.fn().mockResolvedValue(undefined);
  t.delivery.onDelivered = onDelivered;
  for (const [chunk, author] of [
    [-2, "user"],
    [-1, "system"],
  ] as const)
    t.store.enqueueOutbox({
      id: `i${String(chunk)}`,
      turnId: turn.id,
      chunk,
      chatId: "1",
      text: author,
      payload: JSON.stringify({
        kind: "message",
        text: author,
        plain: author,
        author,
      }),
      createdAt: turn.createdAt,
    });
  await t.delivery.flush();
  expect(
    t.store
      .messagesForTurn(turn.id)
      .map((m) => m.author)
      .sort(),
  ).toEqual(["system", "user"]);
  expect(t.store.getCursor("first-delivery-at")).toBeUndefined();
  expect(onDelivered).not.toHaveBeenCalled();
  t.delivery.finish(turn, "completed", "done");
  await t.delivery.flush();
  expect(onDelivered).toHaveBeenCalledOnce();
});
it("retries temporary failures at one second without leaking errors or blocking other turns", async () => {
  const t = setup(),
    first = t.turn("first"),
    second = t.turn("second");
  t.delivery.finish(first, "completed", "answer");
  t.delivery.finish(second, "completed", "next");
  t.failures.push({ code: 500, description: "private tool SECRET" });
  await t.delivery.flush();
  expect(t.store.getOutbox("first/0")).toMatchObject({
    status: "pending",
    error: "server",
    nextAttemptAt: "2026-10-31T12:00:01.000Z",
  });
  expect(t.store.getOutbox("second/0")?.status).toBe("sent");
  expect(JSON.stringify(t.log.mock.calls)).not.toContain("SECRET");
  t.advance(1000);
  await t.delivery.flush();
  expect(t.store.getOutbox("first/0")?.status).toBe("sent");
});

it("uses the complete rich-to-plain-to-document fallback ladder on 400", async () => {
  const t = setup(),
    turn = t.turn("fallback");
  t.delivery.finish(turn, "completed", "**answer**");
  t.failures.push({ code: 400 }, { code: 400 });
  await t.delivery.flush();
  expect(t.calls.map((call) => call.method)).toEqual([
    "sendRichMessage",
    "sendMessage",
    "sendDocument",
  ]);
  expect(t.store.getOutbox("fallback/0")?.status).toBe("sent");
  expect(t.store.messagesForTurn(turn.id)[0]?.text).toBe("**answer**");
});
it("marks forbidden deliveries permanently, permits later turns, and requeues only once", async () => {
  const t = setup(),
    first = t.turn("a"),
    second = t.turn("b");
  t.delivery.finish(first, "completed", "first");
  t.delivery.finish(second, "completed", "second");
  t.failures.push({ code: 403, description: "sensitive" });
  await t.delivery.flush();
  expect(t.store.getOutbox("a/0")).toMatchObject({
    status: "failed",
    error: "forbidden",
  });
  expect(t.store.getOutbox("b/0")?.status).toBe("sent");
  expect(t.store.retryFailed()).toBe(1);
  t.failures.push({ code: 403 });
  await t.delivery.flush();
  expect(t.store.retryFailed()).toBe(0);
  expect(JSON.stringify(t.log.mock.calls)).not.toContain("sensitive");
});
it("persists retry_after and blocks only later chunks of the same answer", async () => {
  const t = setup(),
    first = t.turn("a"),
    second = t.turn("b");
  t.delivery.finish(
    first,
    "completed",
    `${"a".repeat(20000)}\n\n${"b".repeat(20000)}`,
  );
  t.delivery.finish(second, "completed", "independent");
  t.failures.push({ code: 429, after: 7 });
  await t.delivery.flush();
  expect(t.store.getOutbox("a/0")?.nextAttemptAt).toBe(
    "2026-10-31T12:00:07.000Z",
  );
  expect(t.store.getOutbox("a/1")?.status).toBe("pending");
  expect(t.store.getOutbox("b/0")?.status).toBe("sent");
  t.advance(7000);
  await t.delivery.flush();
  expect(t.store.getOutbox("a/1")?.status).toBe("sent");
});
it("recovers an ambiguous send and records possible duplication in history", async () => {
  const t = setup(),
    turn = t.turn("crash");
  t.delivery.finish(turn, "completed", "answer");
  expect(t.store.beginSend("crash/0", turn.createdAt)).toBe(true);
  expect(t.store.recoverSending()).toBe(1);
  await t.delivery.flush();
  expect(t.store.getOutbox("crash/0")).toMatchObject({
    status: "sent",
    possiblyDuplicated: true,
  });
  expect(t.store.messagesForTurn(turn.id)[0]?.possiblyDuplicated).toBe(true);
});
it("fails malformed persisted payload safely and continues with other turns", async () => {
  const t = setup(),
    turn = t.turn("bad"),
    other = t.turn("good");
  t.store.enqueueOutbox({
    id: "bad/0",
    turnId: turn.id,
    chunk: 0,
    chatId: "1",
    text: "",
    payload: "{secret",
    createdAt: turn.createdAt,
  });
  t.delivery.finish(other, "completed", "good");
  await t.delivery.flush();
  expect(t.store.getOutbox("bad/0")).toMatchObject({
    status: "failed",
    error: "invalid-payload",
  });
  expect(t.store.getOutbox("good/0")?.status).toBe("sent");
  expect(JSON.stringify(t.log.mock.calls)).not.toContain("secret");
});

it("keeps delivered chunks and completes only after a later chunk succeeds", async () => {
  const t = setup(),
    turn = t.turn("partial");
  const onDelivered = vi.fn().mockResolvedValue(undefined);
  t.delivery.onDelivered = onDelivered;
  t.delivery.finish(
    turn,
    "completed",
    `${"a".repeat(20000)}\n\n${"b".repeat(20000)}`,
  );
  t.failures.push({ code: 0 }, { code: 500 }, { code: 500 });
  await t.delivery.flush();
  expect(t.store.getOutbox("partial/0")?.status).toBe("sent");
  expect(t.store.getOutbox("partial/1")?.nextAttemptAt).toBe(
    "2026-10-31T12:00:01.000Z",
  );
  expect(onDelivered).not.toHaveBeenCalled();
  t.advance(1000);
  await t.delivery.flush();
  expect(t.store.getOutbox("partial/1")?.nextAttemptAt).toBe(
    "2026-10-31T12:00:03.000Z",
  );
  t.advance(2000);
  await t.delivery.flush();
  expect(onDelivered).toHaveBeenCalledOnce();
  expect(t.store.messagesForTurn(turn.id)).toHaveLength(2);
  expect(t.calls).toHaveLength(4);
});
