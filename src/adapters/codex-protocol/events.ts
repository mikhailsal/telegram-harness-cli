import type { AdapterEvent } from "../contract.ts";
import type { Item, Turn } from "./schema.ts";

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
    return {
      ...event(sessionId, turnId, "output", `item/${item.id}`),
      text: item.text,
    };
  }
  return undefined;
}
export function historyEvents(sessionId: string, turn: Turn): AdapterEvent[] {
  const events = [event(sessionId, turn.id, "turn-started", "start")];
  for (const item of turn.items) {
    const normalized = itemEvent(sessionId, turn.id, item);
    if (normalized) events.push(normalized);
  }
  if (turn.status !== "inProgress")
    events.push({
      ...event(sessionId, turn.id, "turn-completed", "complete"),
      data: { status: turn.status },
    });
  return events;
}
