import { open } from "node:fs/promises";
import type { AdapterEvent } from "../contract.ts";

type RecordData = Record<string, unknown>;
function record(value: unknown): RecordData | undefined {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as RecordData)
    : undefined;
}
function str(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}

export function rolloutEvent(
  line: string,
  sessionId: string,
  cursor: string,
): AdapterEvent | undefined {
  const object = record(JSON.parse(line) as unknown);
  const payload = record(object?.payload);
  if (!payload) return undefined;
  if (object?.type === "response_item")
    return userItem(payload, sessionId, cursor);
  if (object?.type !== "event_msg") return undefined;
  return turnEvent(payload, sessionId, cursor);
}
function userItem(
  payload: RecordData,
  sessionId: string,
  cursor: string,
): AdapterEvent | undefined {
  if (payload.type !== "message" || payload.role !== "user") return undefined;
  const content = Array.isArray(payload.content) ? payload.content : [];
  const metadata = record(payload.internal_chat_message_metadata_passthrough);
  const kinds = Array.isArray(metadata?.content_item_kinds)
    ? metadata.content_item_kinds
    : [];
  if (generatedSetup(kinds)) return undefined;
  const text = content
    .map((item: unknown) => str(record(item)?.text) ?? "")
    .join("");
  if (!text || (!kinds.length && text.startsWith("<environment_context>")))
    return undefined;
  return { kind: "input", sessionId, cursor, text };
}
function generatedSetup(kinds: unknown[]): boolean {
  const names = kinds.filter(
    (kind): kind is string => typeof kind === "string",
  );
  return (
    names.length === kinds.length &&
    names.length > 0 &&
    names.every(
      (kind) =>
        kind === "agents_md.instructions" ||
        kind === "environments.environment_context",
    )
  );
}
function turnEvent(
  payload: RecordData,
  sessionId: string,
  cursor: string,
): AdapterEvent | undefined {
  const turnId = str(payload.turn_id);
  const base =
    turnId === undefined
      ? { sessionId, cursor }
      : { sessionId, cursor, turnId };
  if (payload.type === "agent_reasoning")
    return {
      kind: "progress",
      ...base,
      data: {
        kind: "thinking",
        ...(typeof payload.text === "string"
          ? { detail: payload.text.split("\n")[0] }
          : {}),
      },
    };
  if (payload.type === "task_started") return { kind: "turn-started", ...base };
  if (payload.type === "turn_aborted")
    return {
      kind: "turn-completed",
      ...base,
      data: { status: "aborted", reason: payload.reason },
    };
  if (payload.type === "task_complete") {
    const text = str(payload.last_agent_message);
    return text === undefined
      ? { kind: "turn-completed", ...base }
      : { kind: "turn-completed", ...base, text };
  }
  return undefined;
}

export async function readRollout(
  path: string,
  sessionId: string,
  offset: number,
): Promise<{ events: AdapterEvent[]; offset: number }> {
  if (!Number.isSafeInteger(offset) || offset < 0)
    throw new Error("Invalid rollout cursor");
  const file = await open(path, "r");
  try {
    const stat = await file.stat();
    if (stat.size < offset) throw new Error("Rollout was truncated");
    const size = Math.min(stat.size - offset, 1024 * 1024);
    const buffer = Buffer.alloc(size);
    const { bytesRead } = await file.read(buffer, 0, size, offset);
    const data = buffer.subarray(0, bytesRead);
    const events: AdapterEvent[] = [];
    let index = 0;
    for (;;) {
      const end = data.indexOf(10, index);
      if (end < 0) break;
      const cursor = offset + end + 1;
      const event = rolloutEvent(
        data.subarray(index, end).toString("utf8"),
        sessionId,
        String(cursor),
      );
      if (event) events.push(event);
      index = end + 1;
    }
    if (size === 1024 * 1024 && index === 0)
      throw new Error("Rollout line exceeds read limit");
    return { events, offset: offset + index };
  } finally {
    await file.close();
  }
}

export function isAbort(event: AdapterEvent): boolean {
  const data = record(event.data);
  return data?.status === "aborted";
}
