import type { AdapterEvent } from "../contract.ts";
import type { ProtocolTransport } from "./transport.ts";
import { historyEvents } from "./events.ts";
import { pendingStatus, turnList } from "./schema.ts";

export async function readHistory(
  transport: ProtocolTransport,
  sessionId: string,
): Promise<AdapterEvent[]> {
  let cursor: string | undefined;
  const turns = [];
  do {
    const page = turnList.parse(
      await transport.call("thread/turns/list", {
        threadId: sessionId,
        limit: 100,
        itemsView: "full",
        sortDirection: "asc",
        ...(cursor ? { cursor } : {}),
      }),
    );
    turns.push(...page.data);
    cursor = page.nextCursor ?? undefined;
  } while (cursor);
  return turns.flatMap((turn) => historyEvents(sessionId, turn));
}

export async function readPendingState(
  transport: ProtocolTransport,
  sessionId: string,
): Promise<"none" | "some" | "unknown"> {
  const parsed = pendingStatus.safeParse(
    await transport.call("thread/read", {
      threadId: sessionId,
      includeTurns: false,
    }),
  );
  if (!parsed.success) return "unknown";
  const status = parsed.data.thread.status;
  if (status.type === "idle") return "none";
  if (status.type !== "active") return "unknown";
  return status.activeFlags.length > 0 ? "some" : "none";
}
