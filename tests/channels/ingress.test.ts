import { expect, it } from "vitest";
import type { Update, RichBlock } from "grammy/types";
import { entitiesToMarkdown, parseUpdate } from "../../src/channels/ingress.js";
import {
  richAttachments,
  richBlocksToMarkdown,
} from "../../src/channels/rich-input.js";
const allow = new Set(["1"]);
const chat = { id: 1, type: "private" };
const user = { id: 1, is_bot: false, first_name: "A", language_code: "ru" };
function update(value: unknown): Update {
  return value as Update;
}
it("preserves command, metadata and attachments on authorized messages", () => {
  const command = fixtureCommand();
  expect(command.event).toMatchObject({
    kind: "command",
    command: "mode",
    arguments: "queue",
    language: "ru",
  });
  const message = fixtureMessage();
  expect(message.event).toMatchObject({
    kind: "message",
    reply: "old",
    quote: "quote",
    forward: "hidden_user",
    mediaGroupId: "album",
    attachments: [
      { kind: "photo", fileSize: 10 },
      { kind: "document", fileName: "f.txt" },
    ],
  });
});
it("maps decisions, reactions, draft stop, and silently ignores invalid contexts", () => {
  const callback = fixtureCallback();
  expect(callback.event).toMatchObject({
    kind: "decision",
    data: "accept",
    callbackId: "c",
  });
  const reaction = fixtureReaction();
  expect(reaction.event).toMatchObject({
    kind: "reaction",
    reactions: ["👍", "paid"],
  });
  expect(
    parseUpdate(
      update({
        update_id: 6,
        stopped_message_generation: { chat, draft_id: 8 },
      }),
      allow,
      "en",
    ).event,
  ).toMatchObject({ kind: "generation-stopped", messageId: 8 });
  expect(
    parseUpdate(
      update({
        update_id: 7,
        message: { chat: { id: -1, type: "group" }, from: user },
      }),
      allow,
      "en",
    ),
  ).toEqual({});
  expect(
    parseUpdate(
      update({ update_id: 8, message: { chat, from: { ...user, id: 2 } } }),
      allow,
      "en",
    ),
  ).toEqual({ stranger: { chatId: 1, userId: 2 } });
});
it("maps formatting and complex rich blocks including nested media", () => {
  expect(
    entitiesToMarkdown("link", [
      { type: "text_link", offset: 0, length: 4, url: "https://example.com" },
    ]),
  ).toBe("[link](https://example.com)");
  const blocks = fixtureBlocks();
  expect(richBlocksToMarkdown(blocks)).toContain(
    "Details\n\n> _i_~~s~~`c`\n\ncaption",
  );
  expect(richBlocksToMarkdown(blocks)).toContain("A | B");
  expect(richAttachments(blocks)).toMatchObject([
    { kind: "document", fileName: "a.txt", fileSize: 12 },
  ]);
});

it("retains readable fields and downloadable files from future rich block variants", () => {
  const unknown = [
    {
      type: "future_media",
      title: "Important attachment",
      media: {
        file_id: "f",
        file_unique_id: "u",
        file_name: "future.dat",
        file_size: 5,
      },
    },
  ] as unknown as RichBlock[];
  expect(richBlocksToMarkdown(unknown)).toBe("Important attachment");
  expect(richAttachments(unknown)).toMatchObject([
    {
      kind: "document",
      fileId: "f",
      fileUniqueId: "u",
      fileName: "future.dat",
      fileSize: 5,
    },
  ]);
});

it("recognizes replies to its own bot while keeping the bot ID out of the model metadata", async () => {
  const { TelegramChannel } = await import("../../src/channels/index.js");
  const fetcher: typeof fetch = () =>
    Promise.resolve(
      Response.json({
        ok: true,
        result: [
          {
            update_id: 9,
            message: {
              message_id: 10,
              date: 10,
              chat: { id: 1, type: "private" },
              from: { id: 1, is_bot: false, first_name: "Owner" },
              text: "Why?",
              reply_to_message: {
                message_id: 22,
                date: 5,
                chat: { id: 1, type: "private" },
                from: { id: 123, is_bot: true, first_name: "Bot" },
                text: "Earlier answer",
              },
            },
          },
        ],
      }),
    );
  const channel = new TelegramChannel({
    token: "123:synthetic",
    allowedUserIds: [1],
    fetch: fetcher,
  });
  const result = await channel.poll(0);
  expect(result.events[0]).toMatchObject({
    kind: "message",
    replyAuthor: "assistant",
    replySentAt: "1970-01-01T00:00:05.000Z",
    reply: "Earlier answer",
    replyToMessageId: 22,
    replyToBot: true,
  });
});

it("does not trust a different bot as the configured bot", () => {
  const parsed = parseUpdate(
    update({
      update_id: 30,
      message: {
        chat,
        from: user,
        date: 30,
        message_id: 31,
        text: "Why?",
        reply_to_message: {
          chat,
          date: 20,
          message_id: 21,
          from: { id: 999, is_bot: true, first_name: "Bot" },
          text: "Other answer",
        },
      },
    }),
    allow,
    "en",
    "123",
  );
  expect(parsed.event).toMatchObject({
    kind: "message",
    replyToMessageId: 21,
    reply: "Other answer",
  });
  expect(parsed.event).not.toHaveProperty("replyToBot");
});

function fixtureCommand() {
  return parseUpdate(
    update({
      update_id: 1,
      message: {
        chat,
        from: user,
        date: 1,
        message_id: 2,
        text: "/mode queue",
      },
    }),
    allow,
    "en",
  );
}

function fixtureMessage() {
  return parseUpdate(
    update({
      update_id: 2,
      message: {
        chat,
        from: user,
        date: 1,
        message_id: 3,
        caption: "photo",
        media_group_id: "album",
        quote: { text: "quote" },
        forward_origin: { type: "hidden_user" },
        reply_to_message: { text: "old" },
        photo: [{ file_id: "p", file_unique_id: "u", file_size: 10 }],
        document: { file_id: "d", file_unique_id: "v", file_name: "f.txt" },
      },
    }),
    allow,
    "en",
  );
}

function fixtureCallback() {
  return parseUpdate(
    update({
      update_id: 4,
      callback_query: {
        id: "c",
        from: user,
        data: "accept",
        message: { chat, message_id: 3, date: 1 },
      },
    }),
    allow,
    "en",
  );
}

function fixtureReaction() {
  return parseUpdate(
    update({
      update_id: 5,
      message_reaction: {
        chat,
        user,
        message_id: 3,
        date: 1,
        new_reaction: [{ type: "emoji", emoji: "👍" }, { type: "paid" }],
      },
    }),
    allow,
    "en",
  );
}

function fixtureBlocks() {
  return [
    {
      type: "details",
      summary: "Details",
      blocks: [
        {
          type: "blockquote",
          blocks: [
            {
              type: "paragraph",
              text: [
                { type: "italic", text: "i" },
                { type: "strikethrough", text: "s" },
                { type: "code", text: "c" },
              ],
            },
          ],
        },
        {
          type: "document",
          document: {
            file_id: "f",
            file_unique_id: "u",
            file_name: "a.txt",
            file_size: 12,
          },
          caption: { text: "caption" },
        },
      ],
    },
    {
      type: "table",
      cells: [
        [
          { text: "A", align: "left", valign: "top" },
          { text: "B", align: "left", valign: "top" },
        ],
      ],
    },
    { type: "divider" },
    { type: "anchor", name: "ignored" },
  ] as RichBlock[];
}
