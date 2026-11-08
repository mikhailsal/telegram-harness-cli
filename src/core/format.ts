import type { TelegramMessageFormat } from "../channels/types.js";
import type { Turn } from "../store/index.js";
import type { RuntimeContext } from "./context.js";

export function turnChatId(ctx: RuntimeContext, turn: Turn): string {
  return (
    ctx.store
      .messagesForTurn(turn.id)
      .filter((row) => row.author === "user")
      .at(-1)?.chatId ??
    ctx.store.getCursor("chat-id") ??
    String(ctx.config.allowlist[0])
  );
}
export function chatMessageFormat(
  ctx: RuntimeContext,
  chatId: string,
): TelegramMessageFormat {
  const selected = ctx.store.getCursor(`message-format/${chatId}`);
  if (selected === "rich" || selected === "regular") return selected;
  return ctx.config.format === "regular" ? "regular" : "rich";
}
export function messageFormat(
  ctx: RuntimeContext,
  turn: Turn,
  chatId = turnChatId(ctx, turn),
): TelegramMessageFormat {
  const key = `turn-format/${turn.id}`;
  const existing = ctx.store.getCursor(key);
  if (existing === "rich" || existing === "regular") return existing;
  const format = chatMessageFormat(ctx, chatId);
  ctx.store.setCursor(key, format);
  return format;
}
