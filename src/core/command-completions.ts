import { catalog, transientIntent } from "../channels/index.js";
import type { ChannelEvent } from "../channels/index.js";
import type { RuntimeContext } from "./context.js";
import type { Turn } from "../store/index.js";
import type { AdapterEvent } from "../adapters/contract.js";
import { isCompactionOnly } from "./compaction.js";

type Command = Extract<ChannelEvent, { kind: "command" }>;
type Completion = Pick<Command, "updateId" | "chatId" | "language">;
export function saveCompletion(
  ctx: RuntimeContext,
  key: string,
  event: Command,
): void {
  ctx.store.setCursor(
    key,
    JSON.stringify({
      updateId: event.updateId,
      chatId: event.chatId,
      language: event.language,
    }),
  );
}
export function readCompletion(
  ctx: RuntimeContext,
  key: string,
): Completion | undefined {
  const raw = ctx.store.getCursor(key);
  if (!raw) return undefined;
  try {
    const value: unknown = JSON.parse(raw);
    if (!value || typeof value !== "object") return undefined;
    const item = value as Record<string, unknown>;
    if (
      typeof item.updateId !== "number" ||
      typeof item.chatId !== "string" ||
      (item.language !== "ru" && item.language !== "en")
    )
      return undefined;
    return {
      updateId: item.updateId,
      chatId: item.chatId,
      language: item.language,
    };
  } catch {
    return undefined;
  }
}
export async function confirmCompletion(
  ctx: RuntimeContext,
  item: Completion,
): Promise<void> {
  const strings = catalog[item.language];
  await ctx.transient.send(
    transientIntent(
      `command/${String(item.updateId)}`,
      "command",
      item.chatId,
      ctx.now(),
    ),
    strings.newStarted,
  );
}

export async function confirmDeferredNew(ctx: RuntimeContext): Promise<void> {
  if (ctx.store.getCursor("deferred-new") !== "1") return;
  const command = readCompletion(ctx, "deferred-new-command");
  await ctx.rotateSession(command?.updateId);
  if (command) await confirmCompletion(ctx, command);
  ctx.store.setCursor("deferred-new", "0");
  ctx.store.setCursor("deferred-new-command", "");
}

export async function confirmCompaction(
  ctx: RuntimeContext,
  turn: Turn,
): Promise<void> {
  const key = `compact-completed/${turn.id}`;
  if (
    ctx.store.getCursor(`compact-notice-eligible/${turn.id}`) !== "1" ||
    ctx.store.getCursor(key)
  )
    return;
  const chat = ctx.store.getCursor("chat-id");
  if (!chat) return;
  const language = ctx.store.getCursor("language") === "ru" ? "ru" : "en";
  ctx.store.setCursor(key, "1");
  await ctx.transient.send(
    transientIntent(key, "command", chat, ctx.now()),
    catalog[language].compactCompleted,
  );
}

export function markCompactionNotice(
  ctx: RuntimeContext,
  turn: Turn,
  event: AdapterEvent,
): void {
  if (
    event.kind === "progress" &&
    ["active", "submitting", "uncertain"].includes(turn.status) &&
    isCompactionOnly(ctx, turn)
  )
    ctx.store.setCursor(`compact-notice-eligible/${turn.id}`, "1");
}
