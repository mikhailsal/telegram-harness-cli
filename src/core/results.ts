import { catalog } from "../channels/index.js";
import type { AdapterEvent } from "../adapters/contract.js";
import type { Turn } from "../store/index.js";
import type { RuntimeContext } from "./context.js";
import { continuedTurnId } from "./continuation.js";

type Completion = "completed" | "failed" | "interrupted";
export function completionStatus(event: AdapterEvent): Completion {
  const data =
    typeof event.data === "object" && event.data !== null
      ? (event.data as { status?: unknown })
      : {};
  if (data.status === "aborted" || data.status === "interrupted")
    return "interrupted";
  return data.status === "failed" ? "failed" : "completed";
}
export function completionText(
  ctx: RuntimeContext,
  turn: Turn,
  event: AdapterEvent,
  status: Completion,
): string {
  const strings =
    catalog[ctx.store.getCursor("language") === "ru" ? "ru" : "en"];
  const text =
    event.text ?? ctx.store.getCursor(`turn-output/${turn.id}`) ?? "";
  const final =
    status === "completed"
      ? text || strings.empty
      : status === "interrupted" && event.cursor.startsWith("lost/")
        ? strings.interruptedRestart
        : strings[status];
  return turn.source === "terminal" && !continuedTurnId(ctx.store, turn)
    ? `${ctx.config.non_tg_marker} ${final}`
    : final;
}
