import { catalog, transientIntent } from "../channels/index.js";
import type { ChannelEvent } from "../channels/index.js";
import type { RuntimeContext } from "./context.js";
import { applySetting } from "./settings-audit.js";

type Format = "rich" | "regular";
type Command = Extract<ChannelEvent, { kind: "command" }>;
type Decision = Extract<ChannelEvent, { kind: "decision" }>;
function confirmation(language: "en" | "ru", mode: Format): string {
  const strings = catalog[language];
  const info =
    mode === "rich" ? strings.formatRichInfo : strings.formatRegularInfo;
  return `${strings.format}: ${mode === "rich" ? strings.formatRich : strings.formatRegular}\n\n${info}`;
}
function current(ctx: RuntimeContext, chatId: string): Format {
  const stored = ctx.store.getCursor(`message-format/${chatId}`);
  return stored === "rich" || stored === "regular" ? stored : ctx.config.format;
}
function panel(language: "en" | "ru", mode: Format) {
  const strings = catalog[language];
  const label = mode === "rich" ? strings.formatRich : strings.formatRegular;
  return {
    text: `${strings.format}: ${label}\n\n${strings.formatRichInfo}${mode === "regular" ? `\n\n${strings.formatRegularInfo}` : ""}`,
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
  if (argument === "rich" || argument === "regular") {
    applySetting(ctx, event, "format", argument);
    await ctx.transient.send(
      transientIntent(
        `format/${String(event.updateId)}`,
        "command",
        event.chatId,
        ctx.now(),
      ),
      confirmation(event.language, current(ctx, event.chatId)),
    );
    return;
  }
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
  applySetting(ctx, event, "format", value);
  await ctx.channel.answerCallback(event.callbackId);
  try {
    await ctx.channel.bot.api.editMessageText(
      event.chatId,
      event.messageId,
      confirmation(event.language, current(ctx, event.chatId)),
      { reply_markup: { inline_keyboard: [] } },
    );
  } catch {
    ctx.log("format.panel-edit-failed");
  }
  return true;
}
