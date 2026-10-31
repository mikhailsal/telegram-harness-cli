import { richAttachments, richBlocksToMarkdown } from "./rich-input.js";
import type {
  Update,
  Message,
  MessageEntity,
  MessageOrigin,
  User,
  Chat,
} from "grammy/types";
import type {
  ChannelAttachment,
  ChannelEvent,
  EventBase,
  Language,
} from "./types.js";

export function entitiesToMarkdown(
  text: string,
  entities: readonly MessageEntity[] = [],
): string {
  const opens = new Map<number, string[]>(),
    closes = new Map<number, string[]>();
  for (const entity of entities) {
    const marks: Record<string, [string, string]> = {
      bold: ["**", "**"],
      italic: ["_", "_"],
      strikethrough: ["~~", "~~"],
      code: ["`", "`"],
      pre: ["```\n", "\n```"],
      blockquote: ["> ", ""],
      expandable_blockquote: ["> ", ""],
    };
    let pair = marks[entity.type];
    if (entity.type === "text_link") pair = ["[", `](${entity.url})`];
    if (!pair) continue;
    const start = opens.get(entity.offset) ?? [],
      end = closes.get(entity.offset + entity.length) ?? [];
    start.push(pair[0]);
    end.unshift(pair[1]);
    opens.set(entity.offset, start);
    closes.set(entity.offset + entity.length, end);
  }
  let result = "";
  for (let index = 0; index <= text.length; index++)
    result +=
      (closes.get(index) ?? []).join("") +
      (opens.get(index) ?? []).join("") +
      (text[index] ?? "");
  return result;
}
function attachments(message: Message): ChannelAttachment[] {
  const photo = message.photo?.at(-1);
  const values = [
    ["photo", photo],
    ["document", message.document],
    ["voice", message.voice],
    ["audio", message.audio],
    ["video", message.video],
  ] as const;
  return values.flatMap(([kind, file]) => {
    if (!file) return [];
    const result: ChannelAttachment = {
      kind,
      fileId: file.file_id,
      fileUniqueId: file.file_unique_id,
    };
    if (file.file_size !== undefined) result.fileSize = file.file_size;
    if ("file_name" in file && typeof file.file_name === "string")
      result.fileName = file.file_name;
    return [result];
  });
}
function safeName(value: string | undefined): string | undefined {
  const result = value
    ?.replace(/[\p{Cc}\p{Cf}]/gu, " ")
    .replace(/\s+/gu, " ")
    .trim();
  return result === "" ? undefined : result;
}
function userName(user: User | undefined): string | undefined {
  if (!user) return undefined;
  return safeName([user.first_name, user.last_name].filter(Boolean).join(" "));
}
function chatName(chat: Chat | undefined): string | undefined {
  if (!chat) return undefined;
  return safeName(
    chat.type === "private"
      ? [chat.first_name, chat.last_name].filter(Boolean).join(" ")
      : chat.title,
  );
}
function originName(origin: MessageOrigin): string | undefined {
  switch (origin.type) {
    case "user":
      return userName(origin.sender_user);
    case "hidden_user":
      return safeName(origin.sender_user_name);
    case "chat":
      return safeName(
        [chatName(origin.sender_chat), origin.author_signature]
          .filter(Boolean)
          .join(" · "),
      );
    case "channel":
      return safeName(
        [chatName(origin.chat), origin.author_signature]
          .filter(Boolean)
          .join(" · "),
      );
  }
}
function timestamp(seconds: number | undefined): string | undefined {
  if (typeof seconds !== "number" || !Number.isFinite(seconds))
    return undefined;
  const date = new Date(seconds * 1000);
  return Number.isNaN(date.valueOf()) ? undefined : date.toISOString();
}
function addReplyContext(
  event: Extract<ChannelEvent, { kind: "message" }>,
  message: Message,
  botId?: string,
): void {
  const reply = message.reply_to_message;
  if (reply) {
    addLocalReply(event, reply, botId);
  } else if (message.external_reply?.origin) {
    const origin = message.external_reply.origin;
    const author = originName(origin),
      sentAt = timestamp(origin.date);
    if (author) event.replyAuthor = author;
    if (sentAt) event.replySentAt = sentAt;
  }
}
function addLocalReply(
  event: Extract<ChannelEvent, { kind: "message" }>,
  reply: Message,
  botId?: string,
): void {
  if (Number.isSafeInteger(reply.message_id) && reply.message_id > 0)
    event.replyToMessageId = reply.message_id;
  const ownBot = isOwnBot(reply, botId);
  if (ownBot) event.replyToBot = true;
  event.reply = replyText(reply);
  const author =
    chatName(reply.sender_chat) ??
    (ownBot ? "assistant" : userName(reply.from));
  const sentAt = timestamp(reply.date);
  if (author) event.replyAuthor = author;
  if (sentAt) event.replySentAt = sentAt;
}
function replyText(reply: Message): string {
  return (
    reply.text ??
    reply.caption ??
    (reply.rich_message ? richBlocksToMarkdown(reply.rich_message.blocks) : "")
  );
}
function isOwnBot(reply: Message, botId?: string): boolean {
  return Boolean(
    botId && reply.from?.is_bot && String(reply.from.id) === botId,
  );
}
function addContext(
  event: Extract<ChannelEvent, { kind: "message" }>,
  message: Message,
  botId?: string,
): void {
  addReplyContext(event, message, botId);
  if (message.quote) event.quote = message.quote.text;
  if (message.forward_origin) {
    const origin = message.forward_origin;
    event.forward = origin.type;
    const author = originName(origin),
      sentAt = timestamp(origin.date);
    if (author) event.forwardAuthor = author;
    if (sentAt) event.forwardSentAt = sentAt;
  }
}
function base(
  updateId: number,
  chatId: number,
  userId: number,
  messageId: number,
  date: number,
  language: Language,
): EventBase {
  return {
    updateId,
    chatId: String(chatId),
    userId: String(userId),
    messageId,
    sentAt: new Date(date * 1000).toISOString(),
    language,
  };
}
function messageEvent(
  message: Message,
  updateId: number,
  userId: number,
  language: Language,
  botId?: string,
): { event: ChannelEvent } {
  const common = base(
    updateId,
    message.chat.id,
    userId,
    message.message_id,
    message.date,
    language,
  );
  const text = entitiesToMarkdown(
    message.text ??
      message.caption ??
      (message.rich_message
        ? richBlocksToMarkdown(message.rich_message.blocks)
        : ""),
    message.entities ?? message.caption_entities,
  );
  const command = /^\/([a-z_]+)(?:@\w+)?(?:\s+([\s\S]*))?$/i.exec(text);
  if (command?.[1])
    return {
      event: {
        ...common,
        kind: "command",
        command: command[1].toLowerCase(),
        arguments: command[2] ?? "",
      },
    };
  const event: Extract<ChannelEvent, { kind: "message" }> = {
    ...common,
    kind: "message",
    text,
    attachments: [
      ...attachments(message),
      ...richAttachments(message.rich_message?.blocks ?? []),
    ],
  };
  addContext(event, message, botId);
  if (message.media_group_id) event.mediaGroupId = message.media_group_id;
  return { event };
}
function interactionEvent(
  update: Update,
  chatId: number,
  userId: number,
  language: Language,
): { event?: ChannelEvent } {
  const callback = update.callback_query;
  const reaction = update.message_reaction;
  if (callback?.message && callback.data) {
    const common = base(
      update.update_id,
      chatId,
      userId,
      callback.message.message_id,
      callback.message.date,
      language,
    );
    return {
      event: {
        ...common,
        kind: "decision",
        callbackId: callback.id,
        data: callback.data,
      },
    };
  }
  if (reaction) {
    const common = base(
      update.update_id,
      chatId,
      userId,
      reaction.message_id,
      reaction.date,
      language,
    );
    return {
      event: {
        ...common,
        kind: "reaction",
        reactions: reaction.new_reaction.map((item) =>
          item.type === "emoji" ? item.emoji : item.type,
        ),
      },
    };
  }
  return {};
}
export function parseUpdate(
  update: Update,
  allowed: ReadonlySet<string>,
  fallback: Language,
  botId?: string,
): { event?: ChannelEvent; stranger?: { chatId: number; userId: number } } {
  const message = update.message;
  const stopped = stoppedEvent(update, allowed, fallback);
  if (stopped) return { event: stopped };
  const { chat, user } = updateActors(update);
  if (chat?.type !== "private" || !user || user.is_bot) return {};
  if (!allowed.has(String(user.id)))
    return { stranger: { chatId: chat.id, userId: user.id } };
  const language = userLanguage(user.language_code, fallback);
  if (message)
    return messageEvent(message, update.update_id, user.id, language, botId);
  return interactionEvent(update, chat.id, user.id, language);
}
function updateActors(update: Update) {
  return {
    chat:
      update.message?.chat ??
      update.callback_query?.message?.chat ??
      update.message_reaction?.chat,
    user:
      update.message?.from ??
      update.callback_query?.from ??
      update.message_reaction?.user,
  };
}
function userLanguage(code: string | undefined, fallback: Language): Language {
  return code ? (code.startsWith("ru") ? "ru" : "en") : fallback;
}
function stoppedEvent(
  update: Update,
  allowed: ReadonlySet<string>,
  fallback: Language,
): ChannelEvent | undefined {
  const stopped = update.stopped_message_generation;
  if (!stopped || !allowed.has(String(stopped.chat.id))) return undefined;
  return {
    ...base(
      update.update_id,
      stopped.chat.id,
      stopped.chat.id,
      stopped.draft_id,
      Math.floor(Date.now() / 1000),
      fallback,
    ),
    kind: "generation-stopped",
  };
}
