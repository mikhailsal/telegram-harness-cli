import { expect, it, vi } from "vitest";
import {
  TelegramChannel,
  sendApproval,
  sendQuestion,
  classifyTelegramError,
} from "../../src/channels/index.js";

function transport(fail: Record<string, number> = {}) {
  const calls: { method: string; body: Record<string, unknown> }[] = [];
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
    const code = fail[method];
    if (code)
      return Promise.resolve(
        Response.json({
          ok: false,
          error_code: code,
          description: "failed",
          parameters: { retry_after: 4 },
        }),
      );
    return Promise.resolve(
      Response.json({
        ok: true,
        result:
          method.startsWith("send") &&
          !["sendRichMessageDraft", "sendChatAction"].includes(method)
            ? { message_id: 10, date: 1, chat: { id: 1, type: "private" } }
            : true,
      }),
    );
  };
  return {
    calls,
    channel: new TelegramChannel({
      token: "123:abc",
      allowedUserIds: [1],
      fetch: fetcher,
    }),
  };
}
it("sends escaped approval and question cards with the intended buttons and sound", async () => {
  const api = transport();
  await sendApproval(
    api.channel,
    "1",
    "run <script>",
    "x & y",
    "accept",
    "decline",
    "ru",
    true,
  );
  await sendQuestion(api.channel, "1", "Which?", [{ label: "A", data: "a" }]);
  expect(JSON.stringify(api.calls[0]?.body.rich_message)).toContain(
    "&lt;script&gt;",
  );
  expect(JSON.stringify(api.calls[0]?.body.rich_message)).toContain(
    "x &amp; y",
  );
  expect(api.calls[0]?.body.disable_notification).toBe(true);
  expect(JSON.stringify(api.calls[0]?.body.reply_markup)).toContain("Да");
  expect(JSON.stringify(api.calls[1]?.body.reply_markup)).toContain('"A"');
});
it("exposes edit, deletion, callback, typing, draft and default plus localized menus", async () => {
  const api = transport();
  await api.channel.edit("1", 10, "new");
  await api.channel.delete("1", 10);
  await api.channel.answerCallback("cb", "done");
  await api.channel.typing("1");
  await api.channel.draft(1, 2, "thinking");
  await api.channel.menu();
  expect(api.calls.map((call) => call.method)).toEqual([
    "editMessageText",
    "deleteMessage",
    "answerCallbackQuery",
    "sendChatAction",
    "sendRichMessageDraft",
    "setMyCommands",
    "setMyCommands",
    "setMyCommands",
  ]);
  expect(api.calls[4]?.body.can_stop).toBe(true);
  expect(api.calls.slice(-3).map((call) => call.body.language_code)).toEqual([
    undefined,
    "ru",
    "en",
  ]);
});
it("pairs an animated thinking preview with rich final delivery and keeps its text literal", async () => {
  const api = transport();
  const text = "Thinking <path> **literal**\nnext";
  await api.channel.draft(1, 42, text);
  await api.channel.send("1", "done");
  expect(api.calls.map((call) => call.method)).toEqual([
    "sendRichMessageDraft",
    "sendRichMessage",
  ]);
  expect(api.calls[0]?.body).toMatchObject({
    chat_id: 1,
    draft_id: 42,
    can_stop: true,
    rich_message: { blocks: [{ type: "thinking", text }] },
  });
});
it("tracks only successful live previews and consumes them on direct rich sends", async () => {
  vi.useFakeTimers();
  const api = transport();
  try {
    await api.channel.draft(1, 42, "thinking");
    expect(api.channel.hasDraft("1", 42)).toBe(true);
    expect(api.channel.hasDraft("1", 43)).toBe(false);
    expect(api.channel.hasDraft("2", 42)).toBe(false);
    await vi.advanceTimersByTimeAsync(30000);
    expect(api.channel.hasDraft("1", 42)).toBe(false);
    await api.channel.draft(1, 43, "waiting");
    await api.channel.bot.api.sendRichMessage(2, { markdown: "other chat" });
    expect(api.channel.hasDraft("1", 43)).toBe(true);
    await api.channel.bot.api.sendRichMessage(1, { markdown: "card" });
    expect(api.channel.hasDraft("1", 43)).toBe(false);
  } finally {
    vi.useRealTimers();
  }
});
it("does not invent or consume a preview when its API operation fails", async () => {
  const failedDraft = transport({ sendRichMessageDraft: 429 });
  await expect(failedDraft.channel.draft(1, 42, "thinking")).rejects.toThrow();
  expect(failedDraft.channel.hasDraft("1", 42)).toBe(false);
  const failedAnswer = transport({ sendRichMessage: 429 });
  await failedAnswer.channel.draft(1, 42, "thinking");
  await expect(failedAnswer.channel.send("1", "answer")).rejects.toThrow();
  expect(failedAnswer.channel.hasDraft("1", 42)).toBe(true);
});
it("restores a suppressed reply only for a plain fallback without leaking metadata", async () => {
  const api = transport({ sendRichMessage: 400 });
  await api.channel.deliverChunk(
    "1",
    { kind: "message", text: "answer", plain: "answer" },
    { fallbackReplyToMessageId: 58 },
  );
  expect(api.calls[0]?.body).not.toHaveProperty("reply_parameters");
  expect(api.calls[1]?.body.reply_parameters).toEqual({ message_id: 58 });
  expect(
    api.calls.every((call) => !("fallbackReplyToMessageId" in call.body)),
  ).toBe(true);
});
it("restores the source reply when a rich answer falls back to a document", async () => {
  const api = transport({ sendRichMessage: 400 });
  const payloads: { method: string; payload: unknown }[] = [];
  api.channel.bot.api.config.use((prev, method, payload, signal) => {
    payloads.push({ method, payload });
    return prev(method, payload, signal);
  });
  const text = "x".repeat(5000);
  await api.channel.deliverChunk(
    "1",
    { kind: "message", text, plain: text },
    { fallbackReplyToMessageId: 58 },
  );
  expect(payloads.map(({ method }) => method)).toEqual([
    "sendRichMessage",
    "sendDocument",
  ]);
  expect(payloads[1]?.payload).toMatchObject({
    reply_parameters: { message_id: 58 },
  });
  expect(payloads[1]?.payload).not.toHaveProperty("fallbackReplyToMessageId");
});
it("classifies permanent, throttled, server, and network failures without retrying sends", async () => {
  for (const [code, expected] of [
    [403, "forbidden"],
    [429, "rate-limit"],
    [500, "server"],
    [409, "other"],
  ] as const) {
    const api = transport({ sendRichMessage: code });
    let captured: unknown;
    try {
      await api.channel.send("1", "answer");
    } catch (error) {
      captured = error;
    }
    expect(classifyTelegramError(captured)).toMatchObject({ kind: expected });
    if (code === 429)
      expect(classifyTelegramError(captured).retryAfter).toBe(4);
    expect(api.calls).toHaveLength(1);
  }
  expect(classifyTelegramError(new Error("offline")).kind).toBe("network");
});

it("sends very long approval details as a single document card without truncating", async () => {
  let multipart = "";
  const fakeFetch: typeof fetch = async (_input, init) => {
    multipart = await new Response(init?.body).text();
    return Response.json({
      ok: true,
      result: { message_id: 42, date: 1, chat: { id: 1, type: "private" } },
    });
  };
  const channel = new TelegramChannel({
    token: "123:abc",
    allowedUserIds: [1],
    fetch: fakeFetch,
  });
  const detail = `${"x".repeat(34000)}THE END`;
  expect(
    await sendApproval(channel, "1", "command", detail, "yes", "no", "en"),
  ).toBe(42);
  expect(multipart).toContain(detail);
  expect(multipart).toContain("inline_keyboard");
});

it("keeps a detail over the plain message limit in the expandable rich card", async () => {
  const api = transport();
  const detail = `${"long command ".repeat(700)}end-marker`;
  await sendApproval(api.channel, "1", "command", detail, "yes", "no", "en");
  expect(api.calls[0]?.method).toBe("sendRichMessage");
  const rich = api.calls[0]?.body.rich_message as { html: string };
  expect(rich.html).toContain(detail);
  expect(rich.html).toContain("<blockquote expandable>");
});
