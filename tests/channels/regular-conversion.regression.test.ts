import { Bot } from "grammy";
import { expect, it } from "vitest";
import { sendRegular } from "../../src/channels/regular-delivery.js";
import { TelegramChannel } from "../../src/channels/telegram.js";

const chunk = { text: "<b>answer</b>", plain: "answer" };
const extra = {
  disable_notification: false,
  reply_parameters: { message_id: 12 },
};
function fixture(
  failures: Record<number, { code: number; description: string }> = {},
) {
  const calls: { method: string; body: Record<string, unknown> }[] = [];
  const sequence: string[] = [];
  const fetcher: typeof fetch = (input, init) => {
    const url =
      typeof input === "string"
        ? input
        : input instanceof URL
          ? input.href
          : input.url;
    const method = url.split("/").at(-1) ?? "";
    const body = JSON.parse(
      typeof init?.body === "string" ? init.body : "{}",
    ) as Record<string, unknown>;
    calls.push({ method, body });
    sequence.push(method);
    const failure = failures[calls.length];
    if (failure)
      return Promise.resolve(
        Response.json({
          ok: false,
          error_code: failure.code,
          description: failure.description,
        }),
      );
    return Promise.resolve(
      Response.json({
        ok: true,
        result: { message_id: 172, date: 1, chat: { id: 1, type: "private" } },
      }),
    );
  };
  const api = new Bot("123:synthetic", { client: { fetch: fetcher } }).api;
  return { api, calls, sequence, fetcher };
}
it("wires channel delivery through conversion and the durable checkpoint", async () => {
  const f = fixture();
  const channel = new TelegramChannel({
    token: "123:synthetic",
    allowedUserIds: [1],
    fetch: f.fetcher,
  });
  const result = await channel.deliverChunk(
    "1",
    { kind: "message", style: "html", ...chunk },
    {
      replyToMessageId: 12,
      consumeDraft: true,
      onMessageCreated: (id: number) => {
        f.sequence.push(`checkpoint-${String(id)}`);
      },
    },
  );
  expect(result.messageIds).toEqual([172]);
  expect(f.sequence).toEqual([
    "sendRichMessage",
    "checkpoint-172",
    "editMessageText",
  ]);
});
it("checkpoints the rich message before converting the same ID and preserves reply metadata", async () => {
  const f = fixture();
  const result = await sendRegular(f.api, chunk, {
    chatId: "1",
    extra,
    consumeDraft: true,
    onMessageCreated: (id) => {
      expect(id).toBe(172);
      f.sequence.push("checkpoint");
    },
  });
  expect(result).toEqual({ id: 172, text: "answer" });
  expect(f.sequence).toEqual([
    "sendRichMessage",
    "checkpoint",
    "editMessageText",
  ]);
  expect(f.calls[0]?.body).toMatchObject({
    chat_id: "1",
    reply_parameters: { message_id: 12 },
    rich_message: { html: "<b>answer</b>" },
  });
  expect(f.calls[1]?.body).toMatchObject({
    chat_id: "1",
    message_id: 172,
    text: "<b>answer</b>",
    parse_mode: "HTML",
  });
});
it("retries a failed edit against the checkpointed message without resending", async () => {
  const f = fixture({ 2: { code: 429, description: "retry" } });
  let created: number | undefined;
  await expect(
    sendRegular(f.api, chunk, {
      chatId: "1",
      extra,
      consumeDraft: true,
      onMessageCreated: (id) => {
        created = id;
      },
    }),
  ).rejects.toThrow();
  expect(created).toBe(172);
  if (created === undefined) throw new Error("Missing delivery checkpoint");
  expect(
    await sendRegular(f.api, chunk, {
      chatId: "1",
      extra,
      existingMessageId: created,
    }),
  ).toEqual({ id: 172, text: "answer" });
  expect(f.calls.map((call) => call.method)).toEqual([
    "sendRichMessage",
    "editMessageText",
    "editMessageText",
  ]);
});
it("accepts a not-modified edit as successful crash recovery", async () => {
  const f = fixture({
    1: { code: 400, description: "Bad Request: message is not modified" },
  });
  expect(
    await sendRegular(f.api, chunk, {
      chatId: "1",
      extra,
      existingMessageId: 172,
    }),
  ).toEqual({ id: 172, text: "answer" });
  expect(f.calls.map((call) => call.method)).toEqual(["editMessageText"]);
});
it("falls back to plain text on a rejected rich HTML send and edit", async () => {
  const f = fixture({
    1: { code: 400, description: "bad rich HTML" },
    3: { code: 400, description: "bad edit HTML" },
  });
  expect(
    await sendRegular(f.api, chunk, { chatId: "1", extra, consumeDraft: true }),
  ).toEqual({ id: 172, text: "answer" });
  expect(f.calls.map((call) => call.method)).toEqual([
    "sendRichMessage",
    "sendRichMessage",
    "editMessageText",
    "editMessageText",
  ]);
  expect(f.calls[1]?.body.rich_message).toEqual({
    blocks: [{ type: "paragraph", text: "answer" }],
  });
  expect(f.calls[3]?.body).toMatchObject({ message_id: 172, text: "answer" });
  expect(f.calls[3]?.body).not.toHaveProperty("parse_mode");
});
it("uses regular send directly when there is no draft to consume", async () => {
  const f = fixture({ 1: { code: 400, description: "bad HTML" } });
  expect(await sendRegular(f.api, chunk, { chatId: "1", extra })).toEqual({
    id: 172,
    text: "answer",
  });
  expect(f.calls.map((call) => call.method)).toEqual([
    "sendMessage",
    "sendMessage",
  ]);
  expect(f.calls[1]?.body).toMatchObject({
    text: "answer",
    reply_parameters: { message_id: 12 },
  });
});

it("does not retry or fall back after an ambiguous rich send failure", async () => {
  const f = fixture({ 1: { code: 500, description: "server failure" } });
  let checkpointed = false;
  await expect(
    sendRegular(f.api, chunk, {
      chatId: "1",
      extra,
      consumeDraft: true,
      onMessageCreated: () => {
        checkpointed = true;
      },
    }),
  ).rejects.toThrow();
  expect(f.calls.map((call) => call.method)).toEqual(["sendRichMessage"]);
  expect(checkpointed).toBe(false);
});
