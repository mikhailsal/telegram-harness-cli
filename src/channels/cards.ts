import { InputFile } from "grammy";
import { catalog } from "./catalog.js";
import type { Language } from "./types.js";
import type { TelegramChannel } from "./telegram.js";

function escapeHtml(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}
export async function sendApproval(
  channel: TelegramChannel,
  chatId: string,
  summary: string,
  detail: string,
  acceptData: string,
  declineData: string,
  language: Language,
  silent = false,
): Promise<number> {
  const title = summary.replace(/\s+/g, " ").slice(0, 80);
  const html = `🔐 ${escapeHtml(title)}<blockquote expandable>${escapeHtml(detail)}</blockquote>`;
  const options = {
    disable_notification: silent,
    reply_markup: {
      inline_keyboard: [
        [
          { text: catalog[language].yes, callback_data: acceptData },
          { text: catalog[language].no, callback_data: declineData },
        ],
      ],
    },
  };
  const message =
    html.length <= 32768
      ? await channel.bot.api.sendRichMessage(chatId, { html }, options)
      : await channel.bot.api.sendDocument(
          chatId,
          new InputFile(Buffer.from(detail), "approval.txt"),
          { ...options, caption: `🔐 ${title}` },
        );
  return message.message_id;
}
export async function sendQuestion(
  channel: TelegramChannel,
  chatId: string,
  question: string,
  options: readonly { label: string; data: string }[],
): Promise<number> {
  const message = await channel.bot.api.sendMessage(chatId, question, {
    reply_markup: {
      inline_keyboard: options.map((option) => [
        { text: option.label, callback_data: option.data },
      ]),
    },
  });
  return message.message_id;
}
