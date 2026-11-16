import type {
  AdapterEvent,
  HarnessModel,
  HarnessSettings,
} from "../contract.ts";
import { SettingsUnsupportedError } from "../contract.ts";
import { RpcError } from "./transport.ts";
import type { ProtocolTransport } from "./transport.ts";
import type { Notification } from "./schema.ts";
import { modelList, threadSettings, turnErrorParams } from "./schema.ts";

// All pages share the caller's signal, so one budget covers the whole list.
export async function listModels(
  transport: ProtocolTransport,
  signal: AbortSignal,
): Promise<HarnessModel[]> {
  const models: HarnessModel[] = [];
  let cursor: string | undefined;
  try {
    do {
      const page = modelList.parse(
        await transport.call(
          "model/list",
          { includeHidden: true, limit: 100, ...(cursor ? { cursor } : {}) },
          signal,
        ),
      );
      models.push(...page.data);
      cursor = page.nextCursor ?? undefined;
    } while (cursor);
  } catch (error) {
    if (error instanceof RpcError && error.code === -32601)
      throw new SettingsUnsupportedError();
    throw error;
  }
  return models;
}

export async function readSettings(
  transport: ProtocolTransport,
  threadId: string,
  signal: AbortSignal,
): Promise<HarnessSettings> {
  const { thread } = threadSettings.parse(
    await transport.call(
      "thread/read",
      { threadId, includeTurns: false },
      signal,
    ),
  );
  return {
    ...(thread.model ? { model: thread.model } : {}),
    ...(thread.reasoningEffort ? { effort: thread.reasoningEffort } : {}),
  };
}

// A failed turn's `error` notification becomes a non-fatal `turn-error`;
// the kind `error` stays reserved for a lost connection.
export function turnErrorEvent(
  sessionId: string,
  message: Notification,
): AdapterEvent | undefined {
  if (message.method !== "error") return undefined;
  const parsed = turnErrorParams.safeParse(message.params);
  if (!parsed.success || parsed.data.willRetry) return undefined;
  const info = parsed.data.error.codexErrorInfo;
  return {
    kind: "turn-error",
    sessionId,
    cursor: `turn-error/${parsed.data.turnId}`,
    turnId: parsed.data.turnId,
    text: parsed.data.error.message,
    data: { code: typeof info === "string" ? info : "other" },
  };
}
