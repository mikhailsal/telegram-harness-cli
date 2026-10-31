import { randomUUID } from "node:crypto";
import { catalog } from "../channels/index.js";
import type { ChannelEvent } from "../channels/index.js";
import type { Inbox, Turn } from "../store/index.js";
import type { RuntimeContext } from "./context.js";
import type { MessageEvent } from "./envelope.js";
import { envelope } from "./envelope.js";
import { replyContext } from "./interactions.js";

export function decodeInbox(inbox: Inbox): ChannelEvent {
  const value: unknown = JSON.parse(inbox.payload);
  if (
    !value ||
    typeof value !== "object" ||
    !("kind" in value) ||
    !("updateId" in value)
  )
    throw new Error("Invalid stored inbox event");
  const event = value as ChannelEvent;
  if (
    event.updateId !== inbox.updateId ||
    event.chatId !== inbox.chatId ||
    event.userId !== inbox.userId
  )
    throw new Error("Stored inbox identity mismatch");
  return event;
}
const knownCommands = new Set([
  "stop",
  "status",
  "new",
  "mode",
  "compact",
  "help",
  "start",
]);
export function normalizeEvent(event: ChannelEvent): ChannelEvent {
  if (event.kind !== "command" || knownCommands.has(event.command))
    return event;
  return {
    kind: "message",
    updateId: event.updateId,
    chatId: event.chatId,
    userId: event.userId,
    messageId: event.messageId,
    sentAt: event.sentAt,
    language: event.language,
    text: `/${event.command}${event.arguments ? ` ${event.arguments}` : ""}`,
    attachments: [],
  };
}
export function recordEvent(
  ctx: RuntimeContext,
  incoming: ChannelEvent,
): boolean {
  const event = normalizeEvent(incoming);
  const row = {
    updateId: event.updateId,
    entityId: ctx.entityId,
    chatId: event.chatId,
    userId: event.userId,
    receivedAt: ctx.now().toISOString(),
    payload: JSON.stringify(event),
  };
  if (event.kind !== "message") return ctx.store.recordInbox(row);
  const visible = {
    id: `inbound/${String(event.updateId)}`,
    chatId: event.chatId,
    telegramMessageId: event.messageId,
    author: "user" as const,
    sentAt: event.sentAt,
    text: [
      event.text,
      ...event.attachments.map((file) => file.fileName ?? file.kind),
    ]
      .filter(Boolean)
      .join("\n"),
    turnId: null,
    possiblyDuplicated: false,
  };
  return ctx.store.recordIncoming(row, visible);
}
export function collectMessages(ctx: RuntimeContext): void {
  const inbox = ctx.store.listQueuedInbox();
  const messages = inbox
    .map((row) => ({ row, event: decodeInbox(row) }))
    .filter(
      (item): item is { row: Inbox; event: MessageEvent } =>
        item.event.kind === "message",
    );
  const used = new Set<number>();
  for (const item of messages) {
    if (used.has(item.row.updateId)) continue;
    const group = item.event.mediaGroupId
      ? messages.filter(
          (other) =>
            other.event.chatId === item.event.chatId &&
            other.event.mediaGroupId === item.event.mediaGroupId,
        )
      : [item];
    if (
      item.event.mediaGroupId &&
      ctx.now().getTime() -
        Math.max(...group.map((part) => Date.parse(part.row.receivedAt))) <
        2000
    )
      continue;
    const ids = group.map((part) => part.row.updateId);
    const turn = {
      id: randomUUID(),
      entityId: ctx.entityId,
      source: "telegram" as const,
      createdAt: item.row.receivedAt,
    };
    if (
      ctx.store.queueInbox(
        turn,
        ids,
        ids.map((id) => `inbound/${String(id)}`),
      )
    )
      for (const id of ids) used.add(id);
  }
}
export async function turnText(
  ctx: RuntimeContext,
  turn: Turn,
): Promise<string> {
  const rows = ctx.store
    .listInbox(["assigned"])
    .filter((row) => row.turnId === turn.id);
  const events = rows
    .map(decodeInbox)
    .filter((event): event is MessageEvent => event.kind === "message")
    .map((event) => replyContext(ctx, event));
  const paths: string[] = [];
  for (const event of events)
    for (const file of event.attachments) {
      try {
        const path = await ctx.files.download(
          event.updateId,
          file,
          new Date(event.sentAt),
        );
        ctx.store.assignAttachment(event.updateId, file.fileUniqueId, turn.id);
        paths.push(path);
      } catch (error) {
        paths.push(
          error instanceof Error && error.message === "attachment-too-large"
            ? catalog[event.language].tooLarge
            : catalog[event.language].downloadFailed,
        );
      }
    }
  const reaction = ctx.store.getCursor("next-reaction");
  return envelope(events, paths, reaction, ctx.config.timezone);
}
export function consumeTurnInbox(ctx: RuntimeContext, turnId: string): void {
  for (const row of ctx.store.listInbox(["assigned"]))
    if (row.turnId === turnId) ctx.store.consumeInbox(row.updateId);
}
export function nextQueuedTurn(ctx: RuntimeContext): Turn | undefined {
  const turns = ctx.store
    .listTurns(["queued"])
    .filter((turn) => turn.entityId === ctx.entityId);
  const inbox = ctx.store.listInbox(["assigned"]);
  return turns.sort((left, right) => {
    const first = (id: string): number =>
      Math.min(
        ...inbox.filter((row) => row.turnId === id).map((row) => row.updateId),
        Number.MAX_SAFE_INTEGER,
      );
    return (
      first(left.id) - first(right.id) ||
      left.createdAt.localeCompare(right.createdAt)
    );
  })[0];
}
