import { catalog, fill } from "../channels/index.js";
import type { ChannelEvent } from "../channels/index.js";
import type { RuntimeContext } from "./context.js";
import {
  chooseModel,
  defaultChoice,
  defaultModel,
  effortCard,
  effortLabel,
  findVisible,
  modelCard,
  supports,
} from "./harness-settings-model.js";
import type { CardSnapshot, SettingsState } from "./harness-settings-model.js";
import {
  desiredVersion,
  failureText,
  later,
  precondition,
  readState,
} from "./harness-settings-runner.js";
import type { Outcome, Serialize } from "./harness-settings-runner.js";
import { editCard, readSnapshot, sendText } from "./harness-settings-io.js";
import {
  afterChange,
  commit,
  effortConfirmation,
  modelConfirmation,
  resetLine,
  tierLine,
} from "./harness-settings-reply.js";

type Decision = Extract<ChannelEvent, { kind: "decision" }>;
interface Pick {
  snapshot: CardSnapshot;
  id: string;
  version: string;
}

function liveCard(ctx: RuntimeContext, event: Decision): boolean {
  return ctx.store
    .listTransients()
    .some(
      (row) =>
        row.kind === "command" &&
        row.chatId === event.chatId &&
        row.messageId === event.messageId,
    );
}

// A tap on a model or effort card. The callback is answered at once; the
// card is edited after the reads finish.
export async function decideSettingsCard(
  ctx: RuntimeContext,
  serialize: Serialize | undefined,
  event: Decision,
): Promise<boolean> {
  const match = /^(model|effort):(\d+)$/u.exec(event.data);
  if (!match?.[1] || !match[2]) return false;
  const snapshot = liveCard(ctx, event)
    ? readSnapshot(ctx, event.chatId, event.messageId)
    : undefined;
  const id = snapshot?.ids[Number(match[2])];
  if (snapshot?.kind !== match[1] || id === undefined) {
    await ctx.channel.answerCallback(
      event.callbackId,
      catalog[event.language].stale,
    );
    return true;
  }
  const blocked = precondition(ctx, event.language);
  await ctx.channel.answerCallback(event.callbackId, blocked);
  if (blocked) return true;
  const pick = { snapshot, id, version: desiredVersion(ctx) };
  later(
    ctx,
    serialize,
    (signal) => readState(ctx, signal),
    (outcome) => applyPick(ctx, event, pick, outcome),
  );
  return true;
}

async function applyPick(
  ctx: RuntimeContext,
  event: Decision,
  pick: Pick,
  outcome: Outcome<SettingsState>,
): Promise<void> {
  if (!outcome.ok) {
    await sendText(ctx, event, failureText(ctx, event.language, outcome.error));
    return;
  }
  const state = outcome.value;
  if (desiredVersion(ctx) !== pick.version) {
    await refresh(ctx, event, state, pick, "settingsChanged");
    return;
  }
  if (pick.snapshot.kind === "model") await pickModel(ctx, event, state, pick);
  else await pickEffort(ctx, event, state, pick);
}

// Re-renders the tapped card with a note on top; nothing is written.
async function refresh(
  ctx: RuntimeContext,
  event: Decision,
  state: SettingsState,
  pick: Pick,
  note: "settingsChanged" | "settingsGone",
): Promise<void> {
  const header = fill(event.language, note, { id: pick.id });
  const current =
    state.desired?.model ?? state.running?.model ?? pick.snapshot.model;
  const model =
    pick.snapshot.kind === "effort"
      ? state.models.find((item) => item.id === current)
      : undefined;
  const card = model
    ? effortCard(event.language, state, model)
    : modelCard(event.language, state);
  await editCard(ctx, event, `${header}\n\n${card.text}`, card);
}

async function pickModel(
  ctx: RuntimeContext,
  event: Decision,
  state: SettingsState,
  pick: Pick,
): Promise<void> {
  const model = findVisible(state, pick.id);
  if (!model) {
    await refresh(ctx, event, state, pick, "settingsGone");
    return;
  }
  const choice = chooseModel(state, model);
  commit(ctx, event, choice.settings);
  if (!model.supportedReasoningEfforts.length) {
    await editCard(ctx, event, modelConfirmation(ctx, event, state, choice));
    return;
  }
  const saved: SettingsState = { ...state, desired: choice.settings };
  const effort = choice.settings.effort ?? state.running?.effort;
  const header = [
    fill(event.language, "modelSaved", {
      model: model.id,
      effort: effortLabel(event.language, effort),
    }),
    resetLine(event.language, choice),
    tierLine(ctx, event.language, state, model.id),
    ...afterChange(ctx, event, state),
  ]
    .filter(Boolean)
    .join("\n");
  const card = effortCard(event.language, saved, model, header);
  await editCard(ctx, event, card.text, card);
}

async function pickEffort(
  ctx: RuntimeContext,
  event: Decision,
  state: SettingsState,
  pick: Pick,
): Promise<void> {
  const model = state.models.find((item) => item.id === pick.snapshot.model);
  const current = state.desired?.model ?? state.running?.model;
  if (current !== undefined && current !== pick.snapshot.model) {
    // The card offers another model's efforts; /model ran since it was shown.
    await refresh(ctx, event, state, pick, "settingsChanged");
    return;
  }
  if (!model || !supports(model, pick.id)) {
    await refresh(ctx, event, state, pick, "settingsGone");
    return;
  }
  commit(ctx, event, { ...state.desired, effort: pick.id });
  await editCard(
    ctx,
    event,
    effortConfirmation(ctx, event, state, model.id, pick.id),
  );
}

// The one-tap repair offered by the modelUnavailable notice.
export async function decideRecover(
  ctx: RuntimeContext,
  serialize: Serialize | undefined,
  event: Decision,
): Promise<boolean> {
  if (event.data !== "recover") return false;
  const key = recoverKey(event.chatId, event.messageId);
  const version = recoverVersion(ctx.store.getCursor(key));
  // Stale once used, or once the setting changed after the notice was sent.
  if (version === undefined || version !== desiredVersion(ctx)) {
    await ctx.channel.answerCallback(
      event.callbackId,
      catalog[event.language].stale,
    );
    return true;
  }
  const blocked = precondition(ctx, event.language);
  await ctx.channel.answerCallback(event.callbackId, blocked);
  if (blocked) return true;
  later(
    ctx,
    serialize,
    (signal) => readState(ctx, signal),
    async (outcome) => {
      if (desiredVersion(ctx) !== version) {
        await sendText(ctx, event, catalog[event.language].stale);
        return;
      }
      const model = outcome.ok ? defaultModel(outcome.value) : undefined;
      if (!outcome.ok || !model) {
        const text = outcome.ok
          ? fill(event.language, "noModels")
          : failureText(ctx, event.language, outcome.error);
        await sendText(ctx, event, text);
        return;
      }
      const choice = defaultChoice(model);
      commit(ctx, event, choice.settings);
      ctx.store.setCursor(key, "");
      await editCard(
        ctx,
        event,
        modelConfirmation(ctx, event, outcome.value, choice),
      );
    },
  );
  return true;
}

export const recoverKey = (chatId: string, messageId: number) =>
  `settings-recover/${chatId}/${String(messageId)}`;

// A live recover button stores the desired version it was offered against.
export const recoverValue = (version: string) => JSON.stringify({ version });
function recoverVersion(raw: string | undefined): string | undefined {
  if (!raw) return undefined;
  try {
    const parsed = JSON.parse(raw) as { version?: unknown };
    return typeof parsed.version === "string" ? parsed.version : undefined;
  } catch {
    return undefined;
  }
}
