import { fill } from "../channels/index.js";
import type { ChannelEvent, Language } from "../channels/index.js";
import type { RuntimeContext } from "./context.js";
import type { DesiredSettings } from "./settings-audit.js";
import {
  applySetting,
  desiredSettings,
  effectiveSettings,
} from "./settings-audit.js";
import { effortLabel } from "./harness-settings-model.js";
import type { ModelChoice, SettingsState } from "./harness-settings-model.js";
import { releaseHold } from "./harness-settings-runner.js";

type Event = Pick<
  ChannelEvent,
  "updateId" | "chatId" | "userId" | "sentAt" | "language"
>;

// Every owner change is one audited write. A model or effort choice also
// lifts a queue hold; a fast-mode change keeps the model Codex rejected, so it
// leaves the hold in place. Only /fast sets the service tier; others keep it.
export function commit(
  ctx: RuntimeContext,
  event: Event,
  settings: DesiredSettings,
  liftsHold = true,
): void {
  const tier = desiredSettings(ctx)?.serviceTier;
  applySetting(ctx, event, "harness", {
    ...(tier === undefined ? {} : { serviceTier: tier }),
    ...settings,
  });
  if (liftsHold) releaseHold(ctx);
}

function runningLabel(language: Language, state: SettingsState): string {
  const running = state.running;
  if (!running?.model) return fill(language, "currentSetting");
  return `${running.model} · ${effortLabel(language, running.effort)}`;
}

// Lines shared by every confirmation: what the running turn keeps and, in
// steer mode, that follow-ups join it.
export function afterChange(
  ctx: RuntimeContext,
  event: Event,
  state: SettingsState,
): string[] {
  const active = ctx.store
    .listTurns(["submitting", "active", "uncertain"])
    .some((turn) => turn.entityId === ctx.entityId);
  if (!active) return [];
  const lines = [
    fill(event.language, "keepsCurrentTurn", {
      old: runningLabel(event.language, state),
    }),
  ];
  if (effectiveSettings(ctx, event.chatId).mode === "steer")
    lines.push(fill(event.language, "steerJoins"));
  return lines;
}

export function resetLine(
  language: Language,
  choice: ModelChoice,
): string | undefined {
  if (!choice.reset || !choice.settings.model) return undefined;
  return fill(language, "effortReset", {
    model: choice.settings.model,
    old: choice.reset,
    effort: effortLabel(language, choice.settings.effort),
  });
}

// A model change keeps fast mode on. Say so when the new model does not list
// that tier, since every turn still sends it.
export function tierLine(
  ctx: RuntimeContext,
  language: Language,
  state: SettingsState,
  model: string | undefined,
): string | undefined {
  const tier = desiredSettings(ctx)?.serviceTier;
  if (typeof tier !== "string" || !model) return undefined;
  const listed = state.models
    .find((item) => item.id === model)
    ?.serviceTiers.some((item) => item.id === tier);
  return listed ? undefined : fill(language, "fastUnlisted", { model, tier });
}

export function modelConfirmation(
  ctx: RuntimeContext,
  event: Event,
  state: SettingsState,
  choice: ModelChoice,
): string {
  const effort = choice.settings.effort ?? state.running?.effort;
  return [
    fill(event.language, "modelApplied", {
      model: choice.settings.model ?? "",
      effort: effortLabel(event.language, effort),
    }),
    resetLine(event.language, choice),
    tierLine(ctx, event.language, state, choice.settings.model),
    ...afterChange(ctx, event, state),
  ]
    .filter(Boolean)
    .join("\n");
}

export function effortConfirmation(
  ctx: RuntimeContext,
  event: Event,
  state: SettingsState,
  model: string,
  effort: string,
): string {
  return [
    fill(event.language, "effortApplied", { model, effort }),
    ...afterChange(ctx, event, state),
  ].join("\n");
}

export function resetConfirmation(
  language: Language,
  state: SettingsState,
): string {
  return fill(language, "modelReset", {
    current: runningLabel(language, state),
  });
}
