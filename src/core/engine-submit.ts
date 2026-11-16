import { SteerRejectedError } from "../adapters/contract.js";
import type { Turn } from "../store/index.js";
import type { RuntimeContext } from "./context.js";
import type { TurnStatuses } from "./status.js";
import { messageFormat } from "./format.js";
import { recordExecution } from "./settings-audit.js";
import { acceptReaction, trackReaction } from "./interactions.js";
import { consumeTurnInbox, nextQueuedTurn, turnText } from "./queue.js";

export async function steerTurn(
  ctx: RuntimeContext,
  statuses: TurnStatuses,
  active: Turn,
): Promise<void> {
  const mode = ctx.store.getCursor("followup-mode") ?? ctx.config.mode;
  if (
    mode !== "steer" ||
    !active.externalTurnId ||
    !ctx.adapter.steer ||
    !ctx.adapter.capabilities.has("steer")
  )
    return;
  const turn = nextQueuedTurn(ctx);
  if (!turn) return;
  trackReaction(ctx, turn.id);
  const text = await turnText(ctx, turn);
  ctx.store.setCursor(`followup-of/${turn.id}`, active.id);
  if (
    !ctx.store.transitionTurn(turn.id, "queued", "submitting", {
      sessionId: ctx.sessionId,
      updatedAt: ctx.now().toISOString(),
    })
  )
    return;
  try {
    recordExecution(ctx, turn, "steer");
    await ctx.adapter.steer(
      { correlationId: turn.id, text },
      active.externalTurnId,
    );
    ctx.store.transitionTurn(turn.id, "submitting", "active", {
      externalTurnId: active.externalTurnId,
      updatedAt: ctx.now().toISOString(),
    });
    consumeTurnInbox(ctx, turn.id);
    acceptReaction(ctx, turn.id);
    await statuses.acknowledge();
  } catch (error) {
    if (error instanceof SteerRejectedError) {
      ctx.store.requeueRejectedSteer(turn.id, ctx.now().toISOString());
      return;
    }
    ctx.store.transitionTurn(turn.id, "submitting", "uncertain", {
      updatedAt: ctx.now().toISOString(),
    });
  }
}

export async function submitTurn(
  ctx: RuntimeContext,
  statuses: TurnStatuses,
  turn: Turn,
): Promise<void> {
  messageFormat(ctx, turn);
  trackReaction(ctx, turn.id);
  const text = await turnText(ctx, turn);
  ctx.store.setCursor(`followup-of/${turn.id}`, "");
  if (
    !ctx.store.transitionTurn(turn.id, "queued", "submitting", {
      sessionId: ctx.sessionId,
      updatedAt: ctx.now().toISOString(),
    })
  )
    return;
  ctx.persistAdapter();
  statuses.refresh();
  try {
    recordExecution(ctx, turn, "submit");
    const result = await ctx.adapter.submit({ correlationId: turn.id, text });
    ctx.persistAdapter();
    if (result.turnId)
      ctx.store.transitionTurn(turn.id, "submitting", "active", {
        externalTurnId: result.turnId,
        updatedAt: ctx.now().toISOString(),
      });
    consumeTurnInbox(ctx, turn.id);
    acceptReaction(ctx, turn.id);
  } catch {
    ctx.log("submit_uncertain", { code: "unconfirmed" });
    ctx.store.transitionTurn(turn.id, "submitting", "uncertain", {
      updatedAt: ctx.now().toISOString(),
    });
  }
}
