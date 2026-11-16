import type { MessageEntity } from "grammy/types";
import { transientIntent } from "../channels/index.js";
import type { ChannelEvent } from "../channels/index.js";
import type { RuntimeContext } from "./context.js";
import type { Card, CardSnapshot } from "./harness-settings-model.js";

type Event = Pick<ChannelEvent, "updateId" | "chatId">;
const keyboard = (card: Card | undefined) => ({
  inline_keyboard: (card?.buttons ?? []).map((row) =>
    row.map((button) => ({ text: button.text, callback_data: button.data })),
  ),
});
const snapshotKey = (chatId: string, messageId: number) =>
  `settings-card/${chatId}/${String(messageId)}`;

export function readSnapshot(
  ctx: RuntimeContext,
  chatId: string,
  messageId: number,
): CardSnapshot | undefined {
  const raw = ctx.store.getCursor(snapshotKey(chatId, messageId));
  if (!raw) return undefined;
  return JSON.parse(raw) as CardSnapshot;
}
function saveSnapshot(
  ctx: RuntimeContext,
  chatId: string,
  messageId: number,
  card: Card | undefined,
): void {
  ctx.store.setCursor(
    snapshotKey(chatId, messageId),
    card ? JSON.stringify(card.snapshot) : "",
  );
}

export async function sendText(
  ctx: RuntimeContext,
  event: Event,
  text: string,
  code: string[] = [],
): Promise<void> {
  const entities: MessageEntity[] = [];
  let full = text;
  for (const [index, item] of code.entries()) {
    if (index) full += " ";
    entities.push({ type: "code", offset: full.length, length: item.length });
    full += item;
  }
  await ctx.transient.create(
    transientIntent(
      `settings/${String(event.updateId)}`,
      "command",
      event.chatId,
      ctx.now(),
    ),
    async () =>
      (
        await ctx.channel.bot.api.sendMessage(event.chatId, full, {
          disable_notification: true,
          ...(entities.length ? { entities } : {}),
        })
      ).message_id,
  );
}

export async function showCard(
  ctx: RuntimeContext,
  event: Event,
  card: Card,
  prefix = "settings",
): Promise<void> {
  const messageId = await ctx.transient.create(
    transientIntent(
      `${prefix}/${String(event.updateId)}`,
      "command",
      event.chatId,
      ctx.now(),
    ),
    async () =>
      (
        await ctx.channel.bot.api.sendMessage(event.chatId, card.text, {
          reply_markup: keyboard(card),
          disable_notification: true,
        })
      ).message_id,
  );
  if (messageId !== undefined) saveSnapshot(ctx, event.chatId, messageId, card);
}

// Edits a card after a committed pick; when Telegram refuses the edit, a new
// message carries the same content so a saved change never looks lost.
export async function editCard(
  ctx: RuntimeContext,
  event: Event & { messageId: number },
  text: string,
  card?: Card,
): Promise<void> {
  saveSnapshot(ctx, event.chatId, event.messageId, card);
  try {
    await ctx.channel.bot.api.editMessageText(
      event.chatId,
      event.messageId,
      text,
      { reply_markup: keyboard(card) },
    );
  } catch {
    ctx.log("settings.card-edit-failed");
    if (card) await showCard(ctx, event, { ...card, text }, "settings-retry");
    else await sendText(ctx, event, text);
  }
}
