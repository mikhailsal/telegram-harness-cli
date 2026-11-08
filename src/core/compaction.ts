import type { AdapterEvent } from "../adapters/contract.js";
import type { Turn } from "../store/index.js";
import type { RuntimeContext } from "./context.js";

function isCompactProgress(event: AdapterEvent): boolean {
  return Boolean(
    event.kind === "progress" &&
    event.data &&
    typeof event.data === "object" &&
    "kind" in event.data &&
    event.data.kind === "compacting",
  );
}
export function observeCompaction(
  ctx: RuntimeContext,
  turn: Turn,
  event: AdapterEvent,
): void {
  if (turn.source !== "terminal") return;
  if (event.kind === "turn-started")
    ctx.store.setCursor(`compact-start/${turn.id}`, "1");
  if (
    event.kind === "input" ||
    event.kind === "output" ||
    (event.kind === "turn-completed" && event.text)
  )
    ctx.store.setCursor(`compact-content/${turn.id}`, "1");
  if (isCompactProgress(event))
    ctx.store.setCursor(`compact-turn/${turn.id}`, "1");
}
export function isCompactionOnly(ctx: RuntimeContext, turn: Turn): boolean {
  return (
    turn.source === "terminal" &&
    ctx.store.getCursor(`compact-start/${turn.id}`) === "1" &&
    ctx.store.getCursor(`compact-turn/${turn.id}`) === "1" &&
    ctx.store.getCursor(`compact-content/${turn.id}`) !== "1"
  );
}
