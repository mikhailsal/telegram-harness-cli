import type { RenderChunk, SendOptions } from "../channels/index.js";
import type { Outbox } from "../store/index.js";
import type { RuntimeContext } from "./context.js";
import { replySourceMessageId } from "./reply-target.js";
import { turnDraftId } from "./turn-draft.js";

type Chunk = Pick<RenderChunk, "kind" | "style">;
function conversionOptions(
  ctx: RuntimeContext,
  row: Outbox,
  chunk: Chunk,
): SendOptions {
  if (chunk.style !== "html") return {};
  const turn = ctx.store.getTurn(row.turnId);
  return {
    consumeDraft: Boolean(
      turn &&
      row.chunk >= 0 &&
      ctx.channel.hasDraft(row.chatId, turnDraftId(turn), "rich"),
    ),
    ...(row.messageId === null ? {} : { existingMessageId: row.messageId }),
    onMessageCreated: (messageId: number) => {
      if (!ctx.store.checkpointMessage(row.id, messageId))
        throw new Error("Unable to checkpoint delivered message");
    },
  };
}
function matchingDraft(
  ctx: RuntimeContext,
  row: Outbox,
  chunk: Chunk,
): boolean {
  const turn = ctx.store.getTurn(row.turnId);
  return Boolean(
    turn &&
    chunk.kind === "message" &&
    ctx.channel.hasDraft(
      row.chatId,
      turnDraftId(turn),
      chunk.style === "html" ? "regular" : "rich",
    ),
  );
}
function richDraftOptions(
  ctx: RuntimeContext,
  row: Outbox,
  chunk: Chunk,
): SendOptions {
  if (ctx.config.status !== "draft") return {};
  if (row.chunk < 0 || chunk.kind !== "message" || chunk.style === "html")
    return {};
  const turn = ctx.store.getTurn(row.turnId);
  return turn && matchingDraft(ctx, row, chunk)
    ? { finalDraftId: turnDraftId(turn) }
    : {};
}
// A stored target is an explicit quote. Without one, the first chunk of an
// answer quotes its question unless the question is still the latest message,
// decided when the answer is sent: a message that arrives while the answer
// waits makes the quote necessary.
function replyOptions(ctx: RuntimeContext, row: Outbox): SendOptions {
  if (row.replyToMessageId !== null)
    return { replyToMessageId: row.replyToMessageId };
  if (row.chunk !== 0) return {};
  const turn = ctx.store.getTurn(row.turnId);
  const source = turn && replySourceMessageId(ctx.store, turn, row.chatId);
  return source === undefined ||
    source === ctx.store.latestVisibleMessageId(row.chatId)
    ? {}
    : { replyToMessageId: source };
}
export function deliveryOptions(
  ctx: RuntimeContext,
  row: Outbox,
  chunk: Chunk,
): SendOptions {
  return {
    silent: row.disableNotification,
    ...conversionOptions(ctx, row, chunk),
    ...richDraftOptions(ctx, row, chunk),
    ...replyOptions(ctx, row),
  };
}
