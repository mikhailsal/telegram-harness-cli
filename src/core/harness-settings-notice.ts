import type { AdapterEvent, HarnessSettings } from "../adapters/contract.js";
import { fill } from "../channels/index.js";
import type { Language } from "../channels/index.js";
import type { RuntimeContext } from "./context.js";
import type { Turn } from "../store/index.js";
import { desiredSettings } from "./settings-audit.js";
import { defaultModel, effortLabel } from "./harness-settings-model.js";
import type { SettingsState } from "./harness-settings-model.js";
import {
  desiredVersion,
  holdCursor,
  later,
  readState,
  releaseHold,
} from "./harness-settings-runner.js";
import type { Outcome, Serialize } from "./harness-settings-runner.js";
import { recoverKey, recoverValue } from "./harness-settings-cards.js";

// Codex rejected the model of a turn: hold the queue while that model is
// still desired and offer one tap back to Codex's default. Settings change
// only when the owner taps.
export function noticeModelUnavailable(
  ctx: RuntimeContext,
  serialize: Serialize | undefined,
  event: AdapterEvent,
): void {
  const code = (event.data as { code?: unknown } | undefined)?.code;
  if (code !== "modelNotFound" || !event.turnId) return;
  const key = `settings-notice/${event.turnId}`;
  if (ctx.store.getCursor(key)) return;
  ctx.store.setCursor(key, "1");
  const turn = telegramTurn(ctx, event.turnId);
  // A TUI turn failed: the owner sees it there, and Telegram's own override
  // may well be valid, so Telegram's queue is not held.
  if (!turn) {
    ctx.log("settings.model-unavailable", { model: "unknown", source: "tui" });
    return;
  }
  const failed = rejectedModel(ctx, turn.id);
  ctx.log("settings.model-unavailable", { model: failed ?? "unknown" });
  const chatId = ctx.store.getCursor("chat-id");
  // Without a chat to explain it in, holding the queue would stall it silently.
  if (!chatId) return;
  if (failed) ctx.store.setCursor(holdCursor, failed);
  const language = ctx.store.getCursor("language") === "ru" ? "ru" : "en";
  later(
    ctx,
    serialize,
    (signal) => readState(ctx, signal),
    (outcome) => sendNotice(ctx, { chatId, language, failed }, outcome),
  );
}

// The Telegram turn Codex reported on, if Telegram submitted it.
function telegramTurn(
  ctx: RuntimeContext,
  externalTurnId: string,
): Turn | undefined {
  return ctx.store
    .listTurns(["submitting", "active", "uncertain", "completed", "failed"])
    .find(
      (turn) =>
        turn.entityId === ctx.entityId &&
        turn.source === "telegram" &&
        turn.externalTurnId === externalTurnId,
    );
}

// The model that turn was sent with, from its execution record; desired may
// have moved on since. "none" means the thread's own model was rejected.
function rejectedModel(
  ctx: RuntimeContext,
  turnId: string,
): string | undefined {
  const record = ctx.store.getCursor(`turn-execution/${turnId}/submit`);
  if (record === undefined) return desiredSettings(ctx)?.model;
  try {
    const parsed = JSON.parse(record) as { model?: unknown };
    return typeof parsed.model === "string" && parsed.model !== "none"
      ? parsed.model
      : undefined;
  } catch {
    return undefined;
  }
}

async function sendNotice(
  ctx: RuntimeContext,
  target: { chatId: string; language: Language; failed: string | undefined },
  outcome: Outcome<SettingsState>,
): Promise<void> {
  const state = outcome.ok ? outcome.value : undefined;
  const model = target.failed ?? state?.running?.model ?? "?";
  const fallback = state ? defaultModel(state) : undefined;
  const usable =
    fallback && fallback.id !== target.failed ? fallback : undefined;
  const text = usable
    ? fill(target.language, "modelUnavailable", { model, default: usable.id })
    : fill(target.language, "modelUnavailableManual", { model });
  const message = await ctx.channel.bot.api
    .sendMessage(target.chatId, text, {
      ...(usable
        ? {
            reply_markup: {
              inline_keyboard: [
                [
                  {
                    text: fill(target.language, "recoverButton", {
                      default: usable.id,
                    }),
                    callback_data: "recover",
                  },
                ],
              ],
            },
          }
        : {}),
    })
    .catch((error: unknown) => {
      // No notice reached the owner, so nothing would ever lift the hold.
      releaseHold(ctx);
      throw error;
    });
  if (usable)
    ctx.store.setCursor(
      recoverKey(target.chatId, message.message_id),
      recoverValue(desiredVersion(ctx)),
    );
}

const tier = (language: Language, value: string | null | undefined) =>
  value === undefined
    ? undefined
    : value === null
      ? fill(language, "tierStandard")
      : fill(language, "tierFast", { tier: value });
const described = (language: Language, settings: HarnessSettings) =>
  [
    settings.model,
    settings.effort && `effort ${settings.effort}`,
    tier(language, settings.serviceTier),
  ]
    .filter(Boolean)
    .join(" · ") || effortLabel(language, undefined);

// The model lines of /status: what runs now, what Telegram sets for the next
// turns, and why they differ.
export function settingsStatusLines(
  ctx: RuntimeContext,
  language: Language,
  running: HarnessSettings | undefined,
): string[] {
  const lines = [
    running?.model
      ? fill(language, "statusRunning", {
          model: running.model,
          effort: effortLabel(language, running.effort),
        })
      : fill(language, "statusRunningUnknown"),
  ];
  const desired = desiredSettings(ctx);
  if (!desired) return [...lines, fill(language, "statusNoOverride")];
  lines.push(
    fill(language, "statusNext", { settings: described(language, desired) }),
  );
  const differs =
    running?.model !== undefined &&
    ((desired.model !== undefined && desired.model !== running.model) ||
      (desired.effort !== undefined && desired.effort !== running.effort));
  if (!differs) return lines;
  const sent = ctx.store.getCursor(sentCursor);
  lines.push(
    fill(
      language,
      sent === JSON.stringify(desired) ? "statusDiffers" : "statusPending",
    ),
  );
  return lines;
}
export const sentCursor = "harness-settings/sent";
