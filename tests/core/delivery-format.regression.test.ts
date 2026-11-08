import { expect, it } from "vitest";
import { TelegramChannel } from "../../src/channels/index.js";
import { parseConfig } from "../../src/core/config.js";
import type { RuntimeContext } from "../../src/core/context.js";
import { Delivery } from "../../src/core/delivery.js";
import { openStore } from "../../src/store/index.js";

function transport(
  calls: { method: string; body: Record<string, unknown> }[],
): typeof fetch {
  return (input, init) => {
    const url =
      typeof input === "string"
        ? input
        : input instanceof URL
          ? input.href
          : input.url;
    calls.push({
      method: url.split("/").at(-1) ?? "",
      body: JSON.parse(
        typeof init?.body === "string" ? init.body : "{}",
      ) as Record<string, unknown>,
    });
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
}
it("persists regular format with the outbox chunk even if chat selection changes before retry", async () => {
  const calls: { method: string; body: Record<string, unknown> }[] = [];
  const fetcher = transport(calls);
  const store = openStore(":memory:"),
    now = new Date("2026-11-02T00:00:00Z");
  const turn = seed(store, now);
  const channel = new TelegramChannel({
    token: "123:synthetic",
    allowedUserIds: [1],
    fetch: fetcher,
  });
  const ctx = {
    directory: "/tmp",
    store,
    channel,
    config: parseConfig({
      token: { env: "TOKEN" },
      allowlist: [1],
      sandbox: "read-only",
    }),
    now: () => now,
    log: () => undefined,
  } as unknown as RuntimeContext;
  try {
    const delivery = new Delivery(ctx);
    expect(delivery.finish(turn, "completed", "**formatted**")).toBe(true);
    const [row] = store.listOutbox(["pending"]);
    expect(row?.format).toBe("html");
    expect(JSON.parse(row?.payload ?? "{}")).toMatchObject({ style: "html" });
    store.setCursor("message-format/1", "rich");
    await delivery.flush();
    expect(calls[0]).toMatchObject({
      method: "sendMessage",
      body: { parse_mode: "HTML", text: "<b>formatted</b>" },
    });
    expect(store.messagesForTurn(turn.id)[0]?.text).toBe("formatted");
    enqueueLegacy(store, turn.id, now);
    store.setCursor("message-format/1", "regular");
    await delivery.flush();
    expect(calls[1]?.method).toBe("sendRichMessage");
  } finally {
    store.close();
  }
});
function seed(store: ReturnType<typeof openStore>, now: Date) {
  store.putEntity({
    id: "e",
    directory: "/tmp",
    botId: "123",
    createdAt: now.toISOString(),
  });
  store.createTurn({
    id: "turn",
    entityId: "e",
    source: "telegram",
    createdAt: now.toISOString(),
  });
  store.transitionTurn("turn", "queued", "active");
  store.setCursor("message-format/1", "regular");
  const turn = store.getTurn("turn");
  if (!turn) throw new Error("missing turn");
  return turn;
}
function enqueueLegacy(
  store: ReturnType<typeof openStore>,
  turnId: string,
  now: Date,
): void {
  store.enqueueOutbox({
    id: "legacy",
    turnId,
    chunk: 1,
    chatId: "1",
    text: "legacy",
    createdAt: now.toISOString(),
    format: "rich",
    payload: JSON.stringify({
      kind: "message",
      text: "legacy",
      plain: "legacy",
    }),
  });
}
