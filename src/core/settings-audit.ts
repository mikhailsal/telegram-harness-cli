import { z } from "zod";
import type { ChannelEvent } from "../channels/index.js";
import type { Turn } from "../store/index.js";
import type { RuntimeContext } from "./context.js";
import { chatMessageFormat, messageFormat, turnChatId } from "./format.js";

type Event = Pick<ChannelEvent, "updateId" | "chatId" | "userId" | "sentAt">;
export const desiredCursor = "harness-settings/desired";
const desiredSchema = z
  .object({
    model: z.string().min(1).optional(),
    effort: z.string().min(1).optional(),
    serviceTier: z.string().min(1).nullable().optional(),
  })
  .strict();
export interface DesiredSettings {
  model?: string;
  effort?: string;
  // A tier id from /fast on; null after /fast off.
  serviceTier?: string | null;
}
function clean(value: z.infer<typeof desiredSchema>): DesiredSettings {
  return {
    ...(value.model ? { model: value.model } : {}),
    ...(value.effort ? { effort: value.effort } : {}),
    ...(value.serviceTier === undefined
      ? {}
      : { serviceTier: value.serviceTier }),
  };
}
interface Values {
  mode: "queue" | "steer";
  format: "rich" | "regular";
  harness: DesiredSettings;
}
type Setting = keyof Values;
function oneOf(value: unknown, allowed: readonly string[]): string {
  if (typeof value !== "string" || !allowed.includes(value))
    throw new Error("Invalid setting value");
  return value;
}
// One validator and one cursor per setting; the stored value is a string.
const settings: Record<
  Setting,
  {
    cursor: (chatId: string) => string;
    encode: (value: unknown) => string;
  }
> = {
  mode: {
    cursor: () => "followup-mode",
    encode: (value) => oneOf(value, ["queue", "steer"]),
  },
  format: {
    cursor: (chatId) => `message-format/${chatId}`,
    encode: (value) => oneOf(value, ["rich", "regular"]),
  },
  harness: {
    cursor: () => desiredCursor,
    encode: (value) => {
      const parsed = desiredSchema.safeParse(value);
      if (!parsed.success) throw new Error("Invalid setting value");
      return JSON.stringify(clean(parsed.data));
    },
  },
};

export function applySetting<K extends Setting>(
  ctx: RuntimeContext,
  event: Event,
  setting: K,
  value: Values[K],
): boolean {
  const entry = settings[setting];
  const stored = entry.encode(value);
  const audit = {
    updateId: event.updateId,
    chatId: event.chatId,
    userId: event.userId,
    sentAt: event.sentAt,
    appliedAt: ctx.now().toISOString(),
    setting,
    value,
  };
  const applied = ctx.store.applySetting(
    event.updateId,
    entry.cursor(event.chatId),
    stored,
    JSON.stringify(audit),
  );
  if (applied) ctx.log("setting.applied", audit);
  return applied;
}

// The one reader of the desired harness settings: absent, `{}` and invalid
// values all mean "no Telegram override".
export function desiredSettings(
  ctx: RuntimeContext,
): DesiredSettings | undefined {
  const raw = ctx.store.getCursor(desiredCursor);
  if (!raw) return undefined;
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    value = undefined;
  }
  const parsed = desiredSchema.safeParse(value);
  if (!parsed.success) {
    ctx.log("settings.desired-invalid");
    return undefined;
  }
  const { model, effort, serviceTier } = parsed.data;
  return model || effort || serviceTier !== undefined
    ? clean(parsed.data)
    : undefined;
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
  sent?: DesiredSettings,
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
    model: sent?.model ?? "none",
    effort: sent?.effort ?? "none",
    serviceTier:
      sent?.serviceTier === undefined
        ? "none"
        : (sent.serviceTier ?? "standard"),
    at: ctx.now().toISOString(),
  };
  ctx.store.setCursor(key, JSON.stringify(details));
  ctx.log("turn.execution", details);
}
