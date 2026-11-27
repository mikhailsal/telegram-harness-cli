import type { AdapterEvent } from "../contract.ts";
import type { Item, Turn } from "./schema.ts";
import { itemProgress } from "./progress.ts";

function event(
  sessionId: string,
  turnId: string,
  kind: AdapterEvent["kind"],
  suffix: string,
): AdapterEvent {
  return { sessionId, turnId, kind, cursor: `${turnId}/${suffix}` };
}
export function itemEvent(
  sessionId: string,
  turnId: string,
  item: Item,
): AdapterEvent | undefined {
  if (item.type === "userMessage") {
    const result = event(sessionId, turnId, "input", `item/${item.id}`);
    result.text =
      item.content
        ?.filter((part) => part.type === "text")
        .map((part) => part.text ?? "")
        .join("") ?? "";
    if (item.clientId) result.correlationId = item.clientId;
    return result;
  }
  if (item.type === "agentMessage" && item.text !== undefined) {
    const result: AdapterEvent = {
      ...event(sessionId, turnId, "output", `item/${item.id}`),
      text: item.text,
    };
    // A request_user_input_async question, sent to the owner mid-turn. The
    // item ID is the tool call that the owner's answer names.
    if (item.delivery === "async")
      result.data = {
        delivery: "async",
        itemId: item.id,
        questions: item.questions ?? null,
      };
    return result;
  }
  return undefined;
}
export function historyEvents(sessionId: string, turn: Turn): AdapterEvent[] {
  const started = event(sessionId, turn.id, "turn-started", "start");
  // A turn Codex starts on behalf of an earlier one (for example after its
  // daemon restarted) names the first turn of that chain.
  if (turn.rootTurnId && turn.rootTurnId !== turn.id)
    started.data = { rootTurnId: turn.rootTurnId };
  const events = [started];
  for (const item of turn.items) {
    const normalized = itemEvent(sessionId, turn.id, item);
    if (normalized) events.push(normalized);
    if (item.type === "contextCompaction") {
      const progress = itemProgress(sessionId, turn.id, item);
      if (progress) events.push(progress);
    }
  }
  if (turn.status !== "inProgress")
    events.push({
      ...event(sessionId, turn.id, "turn-completed", "complete"),
      data: { status: turn.status },
    });
  return events;
}
