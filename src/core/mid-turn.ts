import { renderOutgoing } from "../channels/index.js";
import type { RenderChunk } from "../channels/index.js";
import type { TelegramMessageFormat } from "../channels/types.js";
import type { AdapterEvent } from "../adapters/contract.js";
import type { Turn } from "../store/index.js";
import type { RuntimeContext } from "./context.js";
import { messageFormat, turnChatId } from "./format.js";

interface MidTurnMessage {
  marker: string;
  chatId: string;
  text: string;
  author: "assistant" | "user";
  silent: boolean;
  format?: TelegramMessageFormat;
}
function rowFormat(chunk: RenderChunk): "rich" | "html" | "document" {
  if (chunk.kind !== "message") return "document";
  return chunk.style === "html" ? "html" : "rich";
}
// Queues a message Telegram shows while the turn still runs. Negative chunks
// are sent before the final answer, and the marker keeps a replayed event from
// sending the message twice.
export function enqueueMidTurn(
  ctx: RuntimeContext,
  turn: Turn,
  message: MidTurnMessage,
): void {
  if (ctx.store.getCursor(message.marker)) return;
  const tags = ctx.config.markup.tags.map((tag) => tag.name);
  const { chunks } = message.format
    ? renderOutgoing(message.text, tags, ctx.directory, message.format)
    : renderOutgoing(message.text, tags);
  let index = Math.min(
    0,
    ...ctx.store
      .listOutbox(["pending", "sending", "sent", "failed"])
      .filter((row) => row.turnId === turn.id)
      .map((row) => row.chunk),
  );
  for (const [part, chunk] of chunks.entries()) {
    index--;
    ctx.store.enqueueOutbox({
      id: `${message.marker}/${String(part)}`,
      turnId: turn.id,
      chunk: index,
      chatId: message.chatId,
      text: chunk.text,
      createdAt: ctx.now().toISOString(),
      format: rowFormat(chunk),
      payload: JSON.stringify({ ...chunk, author: message.author }),
      disableNotification: message.silent,
    });
  }
  ctx.store.setCursor(message.marker, "1");
}
export function isAsyncOutput(event: AdapterEvent): boolean {
  const data = event.data;
  return (
    event.kind === "output" &&
    typeof data === "object" &&
    data !== null &&
    "delivery" in data &&
    data.delivery === "async"
  );
}
// Codex asks the owner through request_user_input_async: the question arrives
// mid-turn as its own agent message, and the owner's reply comes back as their
// next message. It is sent at once and never becomes the turn's final answer.
// A finished turn's question is stale: replayed history must not resend it.
export function queueAsyncOutput(
  ctx: RuntimeContext,
  turn: Turn,
  event: AdapterEvent,
): void {
  if (!["submitting", "active", "uncertain"].includes(turn.status)) return;
  if (!event.text?.trim()) return;
  const chatId = turnChatId(ctx, turn);
  enqueueMidTurn(ctx, turn, {
    marker: `async/${turn.id}/${event.cursor}`,
    chatId,
    text: event.text,
    author: "assistant",
    silent: false,
    format: messageFormat(ctx, turn, chatId),
  });
}
