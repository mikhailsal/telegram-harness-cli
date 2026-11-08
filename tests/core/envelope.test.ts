import { expect, it } from "vitest";
import type { Update } from "grammy/types";
import { parseUpdate } from "../../src/channels/ingress.js";
import { envelope } from "../../src/core/envelope.js";

function message(input: Record<string, unknown>, botId?: string) {
  const update = {
    update_id: 432109,
    message: {
      message_id: 987654,
      date: 1772953200,
      chat: { id: 33333, type: "private" },
      from: { id: 44444, is_bot: false, first_name: "Owner" },
      ...input,
    },
  } as unknown as Update;
  const event = parseUpdate(update, new Set(["44444"]), "en", botId).event;
  if (event?.kind !== "message") throw new Error("Expected message");
  return event;
}
it("retains forwarded author and original date without exposing Telegram identifiers", () => {
  const event = message({
    text: "Original text\nlast line",
    forward_origin: {
      type: "user",
      date: 1772949600,
      sender_user: {
        id: 912345678,
        is_bot: false,
        first_name: "Alice",
        last_name: "Example",
      },
    },
  });
  const text = envelope([event], [], undefined, "America/New_York");
  expect(text).toContain("[2026-03-08 03:00:00 GMT-4]");
  expect(text).toContain(
    "Forwarded (Alice Example · 2026-03-08 01:00:00 GMT-5)",
  );
  expect(text).toContain("Original text\nlast line");
  for (const id of ["432109", "987654", "33333", "44444", "912345678"])
    expect(text).not.toContain(id);
});
it("prioritizes a selected quote and supplies reply author and local time", () => {
  const event = message(
    {
      text: "Please explain\n\nall of it 🧪\n",
      reply_to_message: {
        message_id: 112233,
        date: 1772949600,
        from: { id: 112244, is_bot: true, first_name: "Bot" },
        text: "full original not selected",
      },
      quote: { text: "selected words" },
    },
    "112244",
  );
  const text = envelope([event], [], undefined, "America/New_York");
  expect(text).toContain(
    "Quote (assistant · 2026-03-08 01:00:00 GMT-5): selected words",
  );
  expect(text).not.toContain("full original not selected");
  expect(text).not.toContain("112233");
  expect(text.endsWith("Please explain\n\nall of it 🧪\n")).toBe(true);
});
it("preserves an unrecorded reply and external reply origin without IDs", () => {
  const event = message({
    text: "Question",
    reply_to_message: {
      message_id: 800111,
      date: 1772949600,
      from: {
        id: 800222,
        is_bot: false,
        first_name: "Jane",
        last_name: "Smith",
      },
      text: "Visible unrecorded answer",
    },
  });
  expect(envelope([event], [], undefined, "UTC")).toContain(
    "Reply (Jane Smith · 2026-03-08 06:00:00 UTC): Visible unrecorded answer",
  );
  const external = message({
    text: "Question",
    quote: { text: "quoted" },
    external_reply: {
      origin: {
        type: "hidden_user",
        date: 1772949600,
        sender_user_name: "Hidden Sender",
      },
    },
  });
  expect(envelope([external], [], undefined, "UTC")).toContain(
    "Quote (Hidden Sender · 2026-03-08 06:00:00 UTC): quoted",
  );
});
it("supports hidden-user and channel forward names without leaking channel IDs", () => {
  const hidden = message({
    text: "Text",
    forward_origin: {
      type: "hidden_user",
      date: 1,
      sender_user_name: "Someone\nElse",
    },
  });
  expect(envelope([hidden], [])).toContain(
    "Forwarded (Someone Else · 1970-01-01 00:00:01 UTC)",
  );
  const channel = message({
    text: "Post",
    forward_origin: {
      type: "channel",
      date: 1,
      message_id: 999888,
      chat: { id: -10012345, type: "channel", title: "News" },
      author_signature: "Editor",
    },
  });
  const text = envelope([channel], []);
  expect(text).toContain("Forwarded (News · Editor · 1970-01-01 00:00:01 UTC)");
  expect(text).not.toContain("999888");
  expect(text).not.toContain("10012345");
});

it("does not misidentify another bot as the assistant", () => {
  const event = message(
    {
      text: "Question",
      reply_to_message: {
        message_id: 1,
        date: 1,
        from: { id: 55555, is_bot: true, first_name: "Other Bot" },
        text: "Other bot's words",
      },
    },
    "112244",
  );
  const text = envelope([event], [], undefined, "UTC");
  expect(text).toContain(
    "Reply (Other Bot · 1970-01-01 00:00:01 UTC): Other bot's words",
  );
  expect(text).not.toContain("55555");
});
it("applies the configured timezone when preparing a queued turn", async () => {
  const { turnText } = await import("../../src/core/queue.js");
  const event = message({ text: "Question" });
  const row = {
    updateId: event.updateId,
    chatId: event.chatId,
    userId: event.userId,
    payload: JSON.stringify(event),
    turnId: "turn",
  };
  const ctx = {
    config: { timezone: "Europe/Moscow" },
    store: { listInbox: () => [row], getCursor: () => undefined },
  };
  const result = await turnText(
    ctx as unknown as import("../../src/core/context.js").RuntimeContext,
    { id: "turn" } as import("../../src/store/index.js").Turn,
  );
  expect(result).toContain("[2026-03-08 10:00:00 GMT+3]");
  expect(result).toContain("Question");
});
