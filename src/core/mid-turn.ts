import { renderOutgoing } from "../channels/index.js";
import type { RenderChunk } from "../channels/index.js";
import type { InlineButton, TelegramMessageFormat } from "../channels/types.js";
import type { Turn } from "../store/index.js";
import type { RuntimeContext } from "./context.js";

interface MidTurnMessage {
  marker: string;
  chatId: string;
  text: string;
  author: "assistant" | "user";
  silent: boolean;
  format?: TelegramMessageFormat;
  /** Buttons under the message's last part. */
  keyboard?: InlineButton[][];
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
      payload: JSON.stringify({
        ...chunk,
        author: message.author,
        ...(message.keyboard && part === chunks.length - 1
          ? { keyboard: message.keyboard }
          : {}),
      }),
      disableNotification: message.silent,
    });
  }
  ctx.store.setCursor(message.marker, "1");
}
