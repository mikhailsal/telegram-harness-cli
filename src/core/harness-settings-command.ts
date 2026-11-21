import { fill } from "../channels/index.js";
import type { ChannelEvent } from "../channels/index.js";
import type { RuntimeContext } from "./context.js";
import {
  chooseModel,
  currentModel,
  defaultChoice,
  defaultModel,
  effortCard,
  findVisible,
  modelCard,
  supports,
  visibleModels,
} from "./harness-settings-model.js";
import type { SettingsState } from "./harness-settings-model.js";
import {
  desiredVersion,
  failureText,
  later,
  precondition,
  readState,
} from "./harness-settings-runner.js";
import type { Serialize } from "./harness-settings-runner.js";
import { sendText, showCard } from "./harness-settings-io.js";
import { fastCommand, fastOn } from "./harness-settings-tier.js";
import {
  commit,
  effortConfirmation,
  modelConfirmation,
  resetConfirmation,
} from "./harness-settings-reply.js";

type Command = Extract<ChannelEvent, { kind: "command" }>;

// /model, /effort and /fast, typed or as a card. Reads run outside the engine
// queue; a write lands only if no other harness change happened meanwhile.
export async function runSettingsCommand(
  ctx: RuntimeContext,
  serialize: Serialize | undefined,
  event: Command,
): Promise<void> {
  const blocked = precondition(ctx, event.language);
  if (blocked) {
    await sendText(ctx, event, blocked);
    return;
  }
  const argument = event.arguments.trim();
  const word = argument.toLowerCase();
  const reset = event.command === "model" && word === "reset";
  const listless = reset || (event.command === "fast" && word === "off");
  const version = desiredVersion(ctx);
  later(
    ctx,
    serialize,
    (signal) => readState(ctx, signal, !listless),
    async (outcome) => {
      if (!outcome.ok) {
        await sendText(
          ctx,
          event,
          failureText(ctx, event.language, outcome.error),
        );
        return;
      }
      if (desiredVersion(ctx) !== version) {
        await sendText(
          ctx,
          event,
          fill(event.language, "settingsChangedTyped"),
        );
        return;
      }
      if (reset) await resetModel(ctx, event, outcome.value);
      else if (event.command === "fast")
        await fastCommand(ctx, event, argument, outcome.value);
      else if (event.command === "model")
        await modelCommand(ctx, event, argument, outcome.value);
      else await effortCommand(ctx, event, argument, outcome.value);
    },
  );
}

async function resetModel(
  ctx: RuntimeContext,
  event: Command,
  state: SettingsState,
): Promise<void> {
  commit(ctx, event, {});
  const text = resetConfirmation(event.language, state);
  await sendText(
    ctx,
    event,
    fastOn(state.desired)
      ? `${text}\n${fill(event.language, "fastKept")}`
      : text,
  );
}

async function modelCommand(
  ctx: RuntimeContext,
  event: Command,
  argument: string,
  state: SettingsState,
): Promise<void> {
  const models = visibleModels(state);
  if (!models.length) {
    await sendText(ctx, event, fill(event.language, "noModels"));
    return;
  }
  if (!argument) {
    await showCard(ctx, event, modelCard(event.language, state));
    return;
  }
  if (argument.toLowerCase() === "default") {
    await pinDefault(ctx, event, state);
    return;
  }
  const model = findVisible(state, argument);
  if (!model) {
    await sendText(
      ctx,
      event,
      fill(event.language, "unknownModel", { input: argument }),
      models.map((item) => item.id),
    );
    return;
  }
  const choice = chooseModel(state, model);
  commit(ctx, event, choice.settings);
  await sendText(ctx, event, modelConfirmation(ctx, event, state, choice));
}

async function pinDefault(
  ctx: RuntimeContext,
  event: Command,
  state: SettingsState,
): Promise<void> {
  const model = defaultModel(state);
  if (!model) {
    await sendText(ctx, event, fill(event.language, "noModels"));
    return;
  }
  const choice = defaultChoice(model);
  commit(ctx, event, choice.settings);
  await sendText(
    ctx,
    event,
    [
      modelConfirmation(ctx, event, state, choice),
      fill(event.language, "defaultPinned", { model: model.id }),
    ].join("\n"),
  );
}

async function effortCommand(
  ctx: RuntimeContext,
  event: Command,
  argument: string,
  state: SettingsState,
): Promise<void> {
  const model = currentModel(state);
  if (!model) {
    await sendText(ctx, event, fill(event.language, "noModels"));
    return;
  }
  const options = model.supportedReasoningEfforts;
  if (!options.length) {
    await sendText(
      ctx,
      event,
      fill(event.language, "noEfforts", { model: model.id }),
    );
    return;
  }
  if (!argument) {
    await showCard(ctx, event, effortCard(event.language, state, model));
    return;
  }
  const effort = argument.toLowerCase();
  if (!supports(model, effort)) {
    await sendText(
      ctx,
      event,
      fill(event.language, "unknownEffort", {
        input: argument,
        model: model.id,
      }),
      options.map((option) => option.reasoningEffort),
    );
    return;
  }
  commit(ctx, event, { ...state.desired, effort });
  await sendText(
    ctx,
    event,
    effortConfirmation(ctx, event, state, model.id, effort),
  );
}
