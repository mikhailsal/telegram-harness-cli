import type { ChannelEvent } from "../channels/index.js";
import type { Turn } from "../store/index.js";
import type { RuntimeContext } from "./context.js";
import { chatMessageFormat, messageFormat, turnChatId } from "./format.js";

type Event = Pick<ChannelEvent, "updateId" | "chatId" | "userId" | "sentAt">;
type Setting = "mode" | "format";
type Value = "queue" | "steer" | "rich" | "regular";

export function applySetting(
  ctx: RuntimeContext,
  event: Event,
  setting: Setting,
  value: Value,
): boolean {
  if (
    (setting === "mode" && value !== "queue" && value !== "steer") ||
    (setting === "format" && value !== "rich" && value !== "regular")
  )
    throw new Error("Invalid setting value");
  const audit = {
    updateId: event.updateId,
    chatId: event.chatId,
    userId: event.userId,
    sentAt: event.sentAt,
    appliedAt: ctx.now().toISOString(),
    setting,
    value,
  };
  const cursor =
    setting === "mode" ? "followup-mode" : `message-format/${event.chatId}`;
  const applied = ctx.store.applySetting(
    event.updateId,
    cursor,
    value,
    JSON.stringify(audit),
  );
  if (applied) ctx.log("setting.applied", audit);
  return applied;
}

export function effectiveSettings(
  ctx: RuntimeContext,
  chatId: string,
): {
  mode: "queue" | "steer";
  format: "rich" | "regular";
} {
  const mode = ctx.store.getCursor("followup-mode");
  return {
    mode: mode === "queue" || mode === "steer" ? mode : ctx.config.mode,
    format: chatMessageFormat(ctx, chatId),
  };
}

export function recordExecution(
  ctx: RuntimeContext,
  turn: Turn,
  action: "submit" | "steer",
): void {
  const chatId = turnChatId(ctx, turn);
  const key = `turn-execution/${turn.id}/${action}`;
  if (ctx.store.getCursor(key)) return;
  const details = {
    turnId: turn.id,
    sessionId: ctx.sessionId,
    chatId,
    action,
    mode: effectiveSettings(ctx, chatId).mode,
    format: messageFormat(ctx, turn, chatId),
    adapter: ctx.config.codex.adapter,
    at: ctx.now().toISOString(),
  };
  ctx.store.setCursor(key, JSON.stringify(details));
  ctx.log("turn.execution", details);
}
