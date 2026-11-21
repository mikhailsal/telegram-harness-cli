import type { HarnessModel } from "../adapters/contract.js";
import { fill } from "../channels/index.js";
import type { ChannelEvent } from "../channels/index.js";
import type { RuntimeContext } from "./context.js";
import { currentModel } from "./harness-settings-model.js";
import type { SettingsState } from "./harness-settings-model.js";
import { sendText } from "./harness-settings-io.js";
import { afterChange, commit } from "./harness-settings-reply.js";

type Command = Extract<ChannelEvent, { kind: "command" }>;
type Tier = HarnessModel["serviceTiers"][number];

// The tier /fast turns on: the one Codex names Fast, else the model's first.
export function fastTier(model: HarnessModel): Tier | undefined {
  return (
    model.serviceTiers.find((tier) => /^fast$/iu.test(tier.name)) ??
    model.serviceTiers[0]
  );
}
export const fastOn = (desired: SettingsState["desired"]) =>
  typeof desired?.serviceTier === "string";
const tierLabel = (tier: Tier) =>
  tier.description ? `${tier.name}: ${tier.description}` : tier.name;

// /fast toggles, /fast on|off sets it. On sends the current model's fast tier
// with every turn; off sends null, which puts the thread back on the
// standard tier, even when Codex's config chose another.
export async function fastCommand(
  ctx: RuntimeContext,
  event: Command,
  argument: string,
  state: SettingsState,
): Promise<void> {
  const wanted = argument.toLowerCase();
  if (wanted && wanted !== "on" && wanted !== "off") {
    await sendText(
      ctx,
      event,
      fill(event.language, "fastUsage", { input: argument }),
    );
    return;
  }
  if (wanted ? wanted === "off" : fastOn(state.desired)) {
    commit(ctx, event, { ...state.desired, serviceTier: null }, false);
    await confirm(ctx, event, state, fill(event.language, "fastOff"));
  } else await turnOn(ctx, event, state);
}

async function turnOn(
  ctx: RuntimeContext,
  event: Command,
  state: SettingsState,
): Promise<void> {
  const model = currentModel(state);
  const tier = model && fastTier(model);
  if (!model || !tier) {
    await sendText(
      ctx,
      event,
      fill(event.language, "noFastTier", {
        model: model?.id ?? state.running?.model ?? "?",
      }),
    );
    return;
  }
  commit(ctx, event, { ...state.desired, serviceTier: tier.id }, false);
  await confirm(
    ctx,
    event,
    state,
    fill(event.language, "fastApplied", {
      model: model.id,
      tier: tierLabel(tier),
    }),
  );
}

async function confirm(
  ctx: RuntimeContext,
  event: Command,
  state: SettingsState,
  text: string,
): Promise<void> {
  await sendText(
    ctx,
    event,
    [text, ...afterChange(ctx, event, state)].join("\n"),
  );
}
