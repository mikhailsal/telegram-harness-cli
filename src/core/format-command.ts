import { catalog, transientIntent } from "../channels/index.js";
import type { ChannelEvent } from "../channels/index.js";
import type { RuntimeContext } from "./context.js";

type Format = "rich" | "regular";
type Command = Extract<ChannelEvent, { kind: "command" }>;
type Decision = Extract<ChannelEvent, { kind: "decision" }>;
function current(ctx: RuntimeContext, chatId: string): Format {
  const stored = ctx.store.getCursor(`message-format/${chatId}`);
  return stored === "rich" || stored === "regular" ? stored : ctx.config.format;
}
function panel(language: "en" | "ru", mode: Format) {
  const strings = catalog[language];
  const label = mode === "rich" ? strings.formatRich : strings.formatRegular;
  return {
    text: `${strings.format}: ${label}\n\n${mode === "rich" ? strings.formatRichInfo : strings.formatRegularInfo}`,
    reply_markup: {
      inline_keyboard: [
        [
          {
            text: `${mode === "rich" ? "✓ " : ""}${strings.formatRich}`,
            callback_data: "format:rich",
          },
          {
            text: `${mode === "regular" ? "✓ " : ""}${strings.formatRegular}`,
            callback_data: "format:regular",
          },
        ],
      ],
    },
  };
}
export async function showFormat(
  ctx: RuntimeContext,
  event: Command,
): Promise<void> {
  const argument = event.arguments.trim().toLowerCase();
  if (argument === "rich" || argument === "regular")
    ctx.store.setCursor(`message-format/${event.chatId}`, argument);
  const content = panel(event.language, current(ctx, event.chatId));
  await ctx.transient.create(
    transientIntent(
      `format/${String(event.updateId)}`,
      "command",
      event.chatId,
      ctx.now(),
    ),
    async () =>
      (
        await ctx.channel.bot.api.sendMessage(event.chatId, content.text, {
          reply_markup: content.reply_markup,
          disable_notification: true,
        })
      ).message_id,
  );
}
export async function decideFormat(
  ctx: RuntimeContext,
  event: Decision,
): Promise<boolean> {
  const match = /^format:(rich|regular)$/u.exec(event.data);
  if (!match?.[1]) return false;
  const card = ctx.store
    .listTransients()
    .find(
      (row) =>
        row.id.startsWith("format/") &&
        row.kind === "command" &&
        row.chatId === event.chatId &&
        row.messageId === event.messageId,
    );
  if (!card) {
    await ctx.channel.answerCallback(
      event.callbackId,
      catalog[event.language].stale,
    );
    return true;
  }
  const value = match[1] === "rich" ? "rich" : "regular";
  ctx.store.setCursor(`message-format/${event.chatId}`, value);
  await ctx.channel.answerCallback(event.callbackId);
  const content = panel(event.language, value);
  try {
    await ctx.channel.bot.api.editMessageText(
      event.chatId,
      event.messageId,
      content.text,
      { reply_markup: content.reply_markup },
    );
  } catch {
    ctx.log("format.panel-edit-failed");
  }
  return true;
}
