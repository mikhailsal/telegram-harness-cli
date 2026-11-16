import type { HarnessModel, HarnessSettings } from "../adapters/contract.js";
import { fill } from "../channels/index.js";
import type { Language } from "../channels/index.js";
import type { DesiredSettings } from "./settings-audit.js";

export const maxModelButtons = 24;
export interface SettingsState {
  models: HarnessModel[];
  running: HarnessSettings | undefined;
  desired: DesiredSettings | undefined;
}
export interface CardSnapshot {
  kind: "model" | "effort";
  ids: string[];
  model?: string;
}
export interface Card {
  text: string;
  buttons: { text: string; data: string }[][];
  snapshot: CardSnapshot;
}
export interface ModelChoice {
  settings: DesiredSettings;
  reset?: string;
}

const same = (left: string, right: string) =>
  left.toLowerCase() === right.toLowerCase();
export function visibleModels(state: SettingsState): HarnessModel[] {
  return state.models.filter((model) => !model.hidden);
}
export function findVisible(
  state: SettingsState,
  input: string,
): HarnessModel | undefined {
  return visibleModels(state).find(
    (model) => same(model.id, input) || same(model.model, input),
  );
}
export function defaultModel(state: SettingsState): HarnessModel | undefined {
  return state.models.find((model) => model.isDefault);
}
// The model the next turn uses: Telegram's choice, else the thread's, else
// Codex's default.
export function currentModel(state: SettingsState): HarnessModel | undefined {
  const id = state.desired?.model ?? state.running?.model;
  if (!id) return defaultModel(state);
  return state.models.find(
    (model) => same(model.id, id) || same(model.model, id),
  );
}
export function currentEffort(state: SettingsState): string | undefined {
  return state.desired?.effort ?? state.running?.effort;
}
export function supports(model: HarnessModel, effort: string): boolean {
  return model.supportedReasoningEfforts.some(
    (option) => option.reasoningEffort === effort,
  );
}
export function chooseModel(
  state: SettingsState,
  model: HarnessModel,
): ModelChoice {
  const effort = currentEffort(state);
  if (effort && supports(model, effort))
    return {
      settings: {
        model: model.id,
        ...(state.desired?.effort ? { effort: state.desired.effort } : {}),
      },
    };
  if (!model.supportedReasoningEfforts.length)
    return { settings: { model: model.id } };
  return {
    settings: { model: model.id, effort: model.defaultReasoningEffort },
    ...(effort ? { reset: effort } : {}),
  };
}
// Codex's default model with its default effort, as `/model default` and the
// recover button pin it.
export function defaultChoice(model: HarnessModel): ModelChoice {
  return {
    settings: {
      model: model.id,
      ...(model.supportedReasoningEfforts.length
        ? { effort: model.defaultReasoningEffort }
        : {}),
    },
  };
}
export function effortLabel(
  language: Language,
  effort: string | undefined,
): string {
  return effort ?? fill(language, "effortDefault");
}

export function modelCard(language: Language, state: SettingsState): Card {
  const current = currentModel(state);
  const shown = visibleModels(state).slice(0, maxModelButtons);
  const lines = [
    fill(language, "modelCard", {
      model: current?.id ?? state.running?.model ?? "unknown",
      effort: effortLabel(language, currentEffort(state)),
    }),
    "",
    ...shown.map(
      (model) => `• ${model.displayName} (${model.id}): ${model.description}`,
    ),
  ];
  if (visibleModels(state).length > shown.length)
    lines.push(fill(language, "otherModels"));
  lines.push("", fill(language, "modelHint"));
  return {
    text: lines.join("\n"),
    buttons: shown.map((model, index) => [
      {
        text: `${model.id === current?.id ? "✓ " : ""}${model.displayName}`,
        data: `model:${String(index)}`,
      },
    ]),
    snapshot: { kind: "model", ids: shown.map((model) => model.id) },
  };
}

export function effortCard(
  language: Language,
  state: SettingsState,
  model: HarnessModel,
  header?: string,
): Card {
  const selected = currentEffort(state);
  const options = model.supportedReasoningEfforts;
  const label = (effort: string) =>
    effort === model.defaultReasoningEffort
      ? `${effort} ${fill(language, "defaultLabel")}`
      : effort;
  const lines = [
    header ?? fill(language, "effortCard", { model: model.id }),
    "",
    ...options.map(
      (option) => `• ${label(option.reasoningEffort)}: ${option.description}`,
    ),
    "",
    fill(language, "effortHint"),
  ];
  return {
    text: lines.join("\n"),
    buttons: options.map((option, index) => [
      {
        text: `${option.reasoningEffort === selected ? "✓ " : ""}${label(option.reasoningEffort)}`,
        data: `effort:${String(index)}`,
      },
    ]),
    snapshot: {
      kind: "effort",
      ids: options.map((option) => option.reasoningEffort),
      model: model.id,
    },
  };
}
