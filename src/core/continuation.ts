import type { AdapterEvent } from "../adapters/contract.js";
import type { Store, Turn } from "../store/index.js";
import type { RuntimeContext } from "./context.js";

// Codex can start a turn on behalf of an earlier one, for example when its
// daemon restarts mid-task and resumes the work. Such a turn is not a terminal
// task: it continues the Telegram turn that its rootTurnId names, so its answer
// belongs under that user's message.
function rootTurnId(event: AdapterEvent): string | undefined {
  const data = event.data as { rootTurnId?: unknown } | undefined;
  return typeof data?.rootTurnId === "string" ? data.rootTurnId : undefined;
}
export function continuedTurnId(store: Store, turn: Turn): string | undefined {
  return store.getCursor(`continues/${turn.id}`);
}
// The turn whose inbound messages route a turn's answer: the continued
// Telegram turn for a continuation, otherwise the turn itself.
export function routingTurnId(store: Store, turnId: string): string {
  return store.getCursor(`continues/${turnId}`) ?? turnId;
}
export function linkContinuation(
  ctx: RuntimeContext,
  turn: Turn,
  event: AdapterEvent,
): void {
  if (event.kind !== "turn-started" || turn.source !== "terminal") return;
  const root = rootTurnId(event);
  if (!root || continuedTurnId(ctx.store, turn)) return;
  const candidates = ctx.store
    .listTurns([
      "active",
      "submitting",
      "uncertain",
      "completed",
      "failed",
      "cancelled",
      "interrupted",
    ])
    .filter(
      (candidate) =>
        candidate.source === "telegram" &&
        candidate.entityId === ctx.entityId &&
        candidate.sessionId === ctx.sessionId &&
        candidate.externalTurnId === root,
    );
  const target =
    candidates.find(
      (candidate) => !ctx.store.getCursor(`followup-of/${candidate.id}`),
    ) ?? candidates[0];
  if (target) ctx.store.setCursor(`continues/${turn.id}`, target.id);
}
