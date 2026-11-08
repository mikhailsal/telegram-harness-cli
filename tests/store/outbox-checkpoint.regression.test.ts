import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { expect, it } from "vitest";
import { openStore } from "../../src/store/index.js";
import { TelegramChannel } from "../../src/channels/index.js";
import { Delivery } from "../../src/core/delivery.js";
import { parseConfig } from "../../src/core/config.js";
import type { RuntimeContext } from "../../src/core/context.js";

function seed(path: string) {
  const store = openStore(path);
  const at = new Date().toISOString();
  store.putEntity({
    id: "e",
    botId: "b",
    directory: "/tmp/checkpoint",
    createdAt: at,
  });
  store.createTurn({
    id: "t",
    entityId: "e",
    source: "telegram",
    createdAt: at,
  });
  store.enqueueOutbox({
    id: "o",
    turnId: "t",
    chunk: 0,
    chatId: "1",
    text: "<b>answer</b>",
    createdAt: at,
    format: "html",
    payload: JSON.stringify({
      kind: "message",
      text: "<b>answer</b>",
      plain: "answer",
      style: "html",
    }),
  });
  store.beginSend("o");
  return store;
}
it("recovers a saved message ID by editing it without sending a duplicate", async () => {
  const directory = mkdtempSync(join(tmpdir(), "thc-checkpoint-"));
  const path = join(directory, "store.db");
  let store = seed(path);
  const calls: string[] = [];
  const channel = new TelegramChannel({
    token: "123:synthetic",
    allowedUserIds: [1],
    fetch: (input) => {
      const url =
        typeof input === "string"
          ? input
          : input instanceof URL
            ? input.href
            : input.url;
      calls.push(url.split("/").at(-1) ?? "");
      return Promise.resolve(
        Response.json({ ok: true, result: { message_id: 172 } }),
      );
    },
  });
  try {
    expect(store.checkpointMessage("o", 172)).toBe(true);
    expect(store.checkpointMessage("o", 173)).toBe(false);
    store.close();
    store = openStore(path);
    expect(store.recoverSending()).toBe(1);
    expect(store.getOutbox("o")?.possiblyDuplicated).toBe(false);
    const context = {
      store,
      channel,
      now: () => new Date(),
      config: parseConfig({
        token: { env: "TOKEN" },
        allowlist: [1],
        sandbox: "read-only",
      }),
      log: () => undefined,
    } as unknown as RuntimeContext;
    await new Delivery(context).flush();
    expect(calls).toEqual(["editMessageText"]);
    expect(store.getOutbox("o")).toMatchObject({
      status: "sent",
      messageId: 172,
      possiblyDuplicated: false,
    });
    expect(store.messagesForTurn("t")).toHaveLength(1);
  } finally {
    store.close();
    rmSync(directory, { recursive: true, force: true });
  }
});

it("refuses to record a different delivered message after a checkpoint", () => {
  const store = seed(":memory:");
  try {
    store.checkpointMessage("o", 172);
    expect(() =>
      store.markSent("o", 173, {
        id: "m",
        chatId: "1",
        telegramMessageId: 173,
        author: "assistant",
        sentAt: new Date().toISOString(),
        text: "answer",
        turnId: "t",
        possiblyDuplicated: false,
      }),
    ).toThrow("differs from its checkpoint");
    expect(store.getOutbox("o")?.status).toBe("sending");
    expect(store.messagesForTurn("t")).toHaveLength(0);
  } finally {
    store.close();
  }
});
