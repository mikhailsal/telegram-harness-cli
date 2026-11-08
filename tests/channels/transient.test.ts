import { describe, expect, it } from "vitest";
import { openStore } from "../../src/store/index.js";
import { TelegramChannel, TransientManager } from "../../src/channels/index.js";

describe("transient persistence", () => {
  it("survives manager restart and removes expired messages", async () => {
    const intent = transientFixture();
    const methods: string[] = [];
    const fakeFetch: typeof fetch = (input) => {
      const url =
        typeof input === "string"
          ? input
          : input instanceof URL
            ? input.href
            : input.url;
      const method = url.split("/").at(-1) ?? "";
      methods.push(method);
      return Promise.resolve(
        Response.json({
          ok: true,
          result:
            method === "deleteMessage"
              ? true
              : { message_id: 7, date: 1, chat: { id: 1, type: "private" } },
        }),
      );
    };
    const store = openStore(":memory:");
    const channel = new TelegramChannel({
      token: "123:abc",
      allowedUserIds: [1],
      fetch: fakeFetch,
    });
    const first = new TransientManager(channel, store);
    await first.send(intent, "status");
    const restarted = new TransientManager(channel, store);
    expect(await restarted.send(intent, "status")).toBe(7);
    await restarted.sweep(new Date("2026-01-01T00:11:00.000Z"));
    expect(methods).toEqual(["sendMessage", "deleteMessage"]);
    expect(store.listTransients()).toEqual([]);
    store.close();
  });
});
function transientFixture() {
  return {
    id: "command",
    kind: "command" as const,
    chatId: "1",
    createdAt: "2026-01-01T00:00:00.000Z",
    expiresAt: "2026-01-01T00:10:00.000Z",
  };
}

it("reposts old cards and removes the prior buttons if deletion fails", async () => {
  const methods: string[] = [];
  const fakeFetch: typeof fetch = (input) => {
    const url =
      typeof input === "string"
        ? input
        : input instanceof URL
          ? input.href
          : input.url;
    const method = url.split("/").at(-1) ?? "";
    methods.push(method);
    if (method === "deleteMessage")
      return Promise.resolve(
        Response.json({ ok: false, error_code: 400, description: "too old" }),
      );
    return Promise.resolve(Response.json({ ok: true, result: true }));
  };
  const store = openStore(":memory:");
  store.putTransient({
    id: "approval",
    kind: "approval",
    chatId: "1",
    createdAt: "2026-01-01T00:00:00.000Z",
    expiresAt: null,
  });
  store.setTransientMessage("approval", 7, "2026-01-01T00:00:00.000Z");
  const manager = new TransientManager(
    new TelegramChannel({
      token: "123:abc",
      allowedUserIds: [1],
      fetch: fakeFetch,
    }),
    store,
  );
  expect(
    await manager.repost("approval", new Date("2026-01-02T23:00:00.000Z"), () =>
      Promise.resolve(8),
    ),
  ).toBe(true);
  expect(store.listTransients()[0]?.messageId).toBe(8);
  expect(methods).toEqual(["deleteMessage", "editMessageReplyMarkup"]);
  store.close();
});

it("persists intents before sending, avoids duplicate sends, and removes command rows by chat", async () => {
  const { transientIntent } = await import("../../src/channels/index.js");
  const fakeFetch: typeof fetch = () =>
    Promise.resolve(Response.json({ ok: true, result: true }));
  const store = openStore(":memory:");
  const manager = new TransientManager(
    new TelegramChannel({
      token: "123:abc",
      allowedUserIds: [1],
      fetch: fakeFetch,
    }),
    store,
  );
  const now = new Date("2026-01-01T00:00:00Z");
  const intent = transientIntent("command", "command", "1", now);
  expect(intent.expiresAt).toBe("2026-01-01T00:10:00.000Z");
  expect(transientIntent("welcome", "welcome", "1", now).expiresAt).toBe(
    "2026-01-02T23:00:00.000Z",
  );
  expect(
    transientIntent("approval", "approval", "1", now).expiresAt,
  ).toBeNull();
  manager.registerExisting(intent, 4);
  manager.registerExisting(intent, 5);
  expect(await manager.create(intent, () => Promise.resolve(6))).toBe(4);
  const other = transientIntent("other", "command", "2", now);
  expect(await manager.create(other, () => Promise.resolve(7))).toBe(7);
  await manager.removeKind("command", "1");
  expect(store.listTransients().map((row) => row.id)).toEqual(["other"]);
  expect(await manager.repost("other", now, () => Promise.resolve(9))).toBe(
    false,
  );
  await manager.remove("missing");
  store.close();
});
