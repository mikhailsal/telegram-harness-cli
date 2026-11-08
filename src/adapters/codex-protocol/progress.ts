import type { AdapterEvent } from "../contract.ts";
import type { Item, Notification } from "./schema.ts";

function readAction(item: Item): string | undefined {
  if (!Array.isArray(item.commandActions)) return;
  for (const value of item.commandActions) {
    if (!value || typeof value !== "object") continue;
    const action = value as { type?: unknown; path?: unknown; name?: unknown };
    if (action.type !== "read") continue;
    if (typeof action.path === "string") return action.path;
    if (typeof action.name === "string") return action.name;
  }
}
export function itemProgress(
  sessionId: string,
  turnId: string,
  item: Item,
): AdapterEvent | undefined {
  let data: { kind: string; detail?: string } | undefined;
  if (item.type === "contextCompaction") data = { kind: "compacting" };
  if (item.type === "reasoning") data = { kind: "thinking" };
  if (item.type === "commandExecution") {
    const read = readAction(item);
    data = read
      ? { kind: "read", detail: read }
      : {
          kind: "command",
          ...(typeof item.command === "string" ? { detail: item.command } : {}),
        };
  }
  if (item.type === "fileChange")
    data = {
      kind: "file-change",
      ...(Array.isArray(item.changes)
        ? { detail: String(item.changes.length) }
        : {}),
    };
  if (!data) return;
  return {
    kind: "progress",
    sessionId,
    turnId,
    cursor: `${turnId}/progress/${item.id}`,
    data,
  };
}

export function reasoningProgress(
  sessionId: string,
  turnId: string,
  text: string,
): AdapterEvent {
  return {
    kind: "progress",
    sessionId,
    turnId,
    cursor: `${turnId}/reasoning`,
    data: { kind: "thinking", detail: text },
  };
}
export class ReasoningProgress {
  private item = "";
  private text = "";
  update(sessionId: string, message: Notification): AdapterEvent | undefined {
    const { turnId, itemId, delta } = message.params;
    if (!turnId || typeof itemId !== "string" || typeof delta !== "string")
      return;
    if (itemId !== this.item) {
      this.item = itemId;
      this.text = "";
    }
    this.text = (this.text + delta).slice(-512);
    const lines = this.text
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean);
    const heading =
      lines.findLast((line) => /^\*\*.+\*\*$/u.test(line)) ?? lines[0] ?? "";
    return reasoningProgress(
      sessionId,
      turnId,
      heading.replace(/^\*\*|\*\*$/gu, ""),
    );
  }
}
