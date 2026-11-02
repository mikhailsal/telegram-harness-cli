import type { TelegramChannel } from "./telegram.js";

type Log = (event: string, details: Record<string, unknown>) => void;
const methods = new Set([
  "sendMessage",
  "sendRichMessage",
  "sendMessageDraft",
  "sendRichMessageDraft",
  "sendPhoto",
  "sendDocument",
  "editMessageText",
  "deleteMessage",
]);
function fields(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object") return {};
  const row = value as Record<string, unknown>;
  const result: Record<string, unknown> = {};
  for (const key of ["chat_id", "message_id", "draft_id"])
    if (typeof row[key] === "string" || typeof row[key] === "number")
      result[key] = row[key];
  return result;
}
export function logTelegramActivity(channel: TelegramChannel, log: Log): void {
  channel.bot.api.config.use(async (previous, method, payload, signal) => {
    const details = fields(payload);
    if (!methods.has(method)) return previous(method, payload, signal);
    const start = Date.now();
    const base = {
      method,
      ...details,
      startedAt: new Date(start).toISOString(),
    };
    try {
      const result = await previous(method, payload, signal);
      log("telegram.api", {
        ...base,
        elapsedMs: Date.now() - start,
        ok: result.ok,
        ...(result.ok ? fields(result.result) : { code: result.error_code }),
      });
      return result;
    } catch (error) {
      log("telegram.api", {
        ...base,
        elapsedMs: Date.now() - start,
        ok: false,
        outcome: "unknown",
      });
      throw error;
    }
  });
}
