import type { VisibleMessage } from "../../src/store/index.ts";

export interface ExpectedInput {
  turnId: string;
  marker: string;
  envelope?: string;
}
export interface HistorySnapshot {
  items?: unknown[];
  records?: unknown[];
  tools: unknown[];
}
function object(value: unknown): Record<string, unknown> | undefined {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined;
}
export function assertProtocolInputs(
  items: readonly unknown[],
  expected: readonly ExpectedInput[],
): void {
  for (const { turnId, marker } of expected) {
    const matching = items.filter(
      (item) =>
        object(item)?.type === "userMessage" &&
        object(item)?.clientId === turnId,
    );
    if (matching.length !== 1 || !JSON.stringify(matching[0]).includes(marker))
      throw new Error(
        `Protocol history did not contain exactly one matching input for ${turnId}`,
      );
  }
}
export function rolloutInputs(records: readonly unknown[]): string[] {
  return records.flatMap((record) => {
    const row = object(record),
      payload = object(row?.payload);
    if (
      row?.type !== "response_item" ||
      payload?.type !== "message" ||
      payload.role !== "user"
    )
      return [];
    const content: unknown[] = Array.isArray(payload.content)
      ? payload.content
      : [];
    return [
      content
        .map((item) => {
          const text = object(item)?.text;
          return typeof text === "string" ? text : "";
        })
        .join(""),
    ];
  });
}
export function assertTmuxInputs(
  records: readonly unknown[],
  expected: readonly ExpectedInput[],
): void {
  const inputs = rolloutInputs(records);
  for (const { turnId, marker, envelope } of expected)
    if (
      !envelope ||
      !envelope.includes(marker) ||
      inputs.filter((text) => text === envelope).length !== 1
    )
      throw new Error(
        `Tmux rollout did not contain exactly one matching input for ${turnId}`,
      );
}
export function assertOrderedAnswers(
  messages: readonly Pick<VisibleMessage, "author" | "turnId" | "text">[],
  expected: readonly ExpectedInput[],
): void {
  let previous = -1;
  for (const { turnId, marker } of expected) {
    const indexes = messages.flatMap((row, index) =>
      row.author === "assistant" &&
      row.turnId === turnId &&
      row.text.includes(marker)
        ? [index]
        : [],
    );
    const index = indexes[0];
    if (indexes.length !== 1 || index === undefined || index <= previous)
      throw new Error(
        `Missing, duplicated or out-of-order answer for ${turnId}`,
      );
    previous = index;
  }
}
export function hasToolAttempt(
  records: readonly unknown[],
  command: string,
): boolean {
  return records.some((record) => JSON.stringify(record).includes(command));
}
function payload(record: unknown): Record<string, unknown> {
  const value = object(record);
  return object(value?.payload) ?? value ?? {};
}
function recordId(record: unknown): unknown {
  const data = payload(record);
  return data.call_id ?? data.callId ?? data.id;
}
export function hasRunningTool(
  history: HistorySnapshot,
  command: string,
): boolean {
  if (history.items)
    return history.tools.some(
      (item) =>
        JSON.stringify(item).includes(command) &&
        ["inProgress", "running"].includes(String(object(item)?.status)),
    );
  const starts = history.tools.filter(
    (record) =>
      payload(record).type === "exec_command_begin" &&
      recordId(record) &&
      JSON.stringify(record).includes(command),
  );
  if (starts.length)
    return starts.some(
      (start) =>
        !history.tools.some(
          (record) =>
            recordId(record) === recordId(start) &&
            payload(record).type === "exec_command_end",
        ),
    );
  return history.tools.some((record) => {
    const type = payload(record).type,
      id = recordId(record);
    if (
      !id ||
      typeof type !== "string" ||
      !["function_call", "custom_tool_call", "exec_command_begin"].includes(
        type,
      ) ||
      !JSON.stringify(record).includes(command)
    )
      return false;
    return !history.tools.some(
      (other) =>
        recordId(other) === id &&
        [
          "function_call_output",
          "custom_tool_call_output",
          "exec_command_end",
        ].includes(String(payload(other).type)),
    );
  });
}
function denial(record: unknown): boolean {
  return /permission denied|operation not permitted|read-only file system|sandbox denied|exit(?:_code| code)[\\"\s:=-]+[1-9]|exited with code [1-9]/i.test(
    JSON.stringify(record),
  );
}
export function assertDenied(
  records: readonly unknown[],
  target: string,
): void {
  const attempts = records.filter((record) =>
    JSON.stringify(record).includes(target),
  );
  if (!attempts.length)
    throw new Error("Tmux did not attempt the forbidden operation");
  const proved = attempts.some(
    (attempt) =>
      denial(attempt) ||
      (recordId(attempt) !== undefined &&
        records.some(
          (record) => recordId(record) === recordId(attempt) && denial(record),
        )),
  );
  if (!proved)
    throw new Error(
      "Tmux tool history did not prove denial for the attempted command",
    );
}
export function approvalCallback(
  updateId: number,
  messageId: number,
  data: string,
) {
  return {
    update_id: updateId,
    callback_query: {
      id: `live-${String(updateId)}`,
      from: {
        id: 1,
        is_bot: false,
        first_name: "Acceptance",
        language_code: "en",
      },
      data,
      message: {
        message_id: messageId,
        date: Math.floor(Date.now() / 1000),
        chat: { id: 1, type: "private" },
      },
      chat_instance: "live",
    },
  };
}
export class ProtocolCommandMonitor {
  private readonly thread: string;
  private readonly commands = new Map<string, unknown>();
  constructor(thread: string) {
    this.thread = thread;
  }
  receive(raw: unknown): void {
    const message = object(raw),
      params = object(message?.params),
      item = object(params?.item);
    if (
      params?.threadId !== this.thread ||
      item?.type !== "commandExecution" ||
      typeof item.id !== "string"
    )
      return;
    if (message?.method === "item/completed") this.commands.delete(item.id);
    else if (
      message?.method === "item/started" ||
      message?.method === "item/updated"
    )
      this.commands.set(item.id, item);
  }
  active(): unknown[] {
    return [...this.commands.values()];
  }
}
