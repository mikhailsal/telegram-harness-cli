import type { ChannelEvent } from "../channels/index.js";
import type { VisibleMessage } from "../store/index.js";
import type { RuntimeContext } from "./context.js";
import type { MessageEvent } from "./envelope.js";

type Reaction = Extract<ChannelEvent, { kind: "reaction" }>;
function excerpt(text: string, limit = 160): string {
  const clean = text
    .replace(/[\p{Cc}\p{Cf}]/gu, " ")
    .replace(/\s+/gu, " ")
    .trim();
  const chars = Array.from(clean);
  return chars.length > limit ? `${chars.slice(0, limit).join("")}…` : clean;
}
function assistantMessage(
  ctx: RuntimeContext,
  chat: string,
  message: number,
): VisibleMessage | undefined {
  const row = ctx.store.visibleMessage(chat, message);
  return row?.author === "assistant" ? row : undefined;
}
export function replyContext(
  ctx: RuntimeContext,
  event: MessageEvent,
): MessageEvent {
  if (!event.replyToBot || event.replyToMessageId === undefined) return event;
  const target = assistantMessage(ctx, event.chatId, event.replyToMessageId);
  if (!target?.turnId) return event;
  const source = ctx.store
    .messagesForTurn(target.turnId)
    .filter(
      (row) =>
        row.author === "user" &&
        row.chatId === event.chatId &&
        row.telegramMessageId > 0,
    )
    .at(-1);
  return {
    ...event,
    reply: target.text,
    replyAuthor: "assistant",
    replySentAt: target.sentAt,
    ...(source
      ? {
          replySource: {
            sentAt: source.sentAt,
            text: excerpt(source.text, 200),
          },
        }
      : {}),
  };
}
function reactionDescription(event: Reaction, target: VisibleMessage): string {
  const reactions = excerpt(event.reactions.join(" "));
  const label =
    event.language === "ru"
      ? reactions
        ? `Реакция ${reactions}`
        : "Реакция снята"
      : reactions
        ? `Reacted ${reactions}`
        : "Reaction removed";
  const targetLabel = event.language === "ru" ? "на ответ" : "to answer";
  return `${label} ${targetLabel} ${target.sentAt}: ${excerpt(target.text)}`;
}
export function recordReaction(ctx: RuntimeContext, event: Reaction): void {
  const target = assistantMessage(ctx, event.chatId, event.messageId);
  if (!target) {
    ctx.store.recordReactionControl(event.updateId);
    return;
  }
  const text = reactionDescription(event, target);
  ctx.store.recordReactionControl(
    event.updateId,
    {
      id: `reaction/${String(event.updateId)}`,
      chatId: event.chatId,
      author: "user",
      sentAt: event.sentAt,
      text,
      turnId: target.turnId,
      possiblyDuplicated: false,
    },
    text,
  );
}
export function trackReaction(ctx: RuntimeContext, turnId: string): void {
  ctx.store.captureReaction(turnId);
}
export function acceptReaction(ctx: RuntimeContext, turnId: string): void {
  ctx.store.acceptReaction(turnId);
}
