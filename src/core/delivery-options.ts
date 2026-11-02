import type { RenderChunk, SendOptions } from "../channels/index.js";
import type { Outbox } from "../store/index.js";
import type { RuntimeContext } from "./context.js";
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
export function deliveryOptions(
  ctx: RuntimeContext,
  row: Outbox,
  chunk: Chunk,
  automaticReply: boolean | undefined,
): SendOptions {
  const conversion = conversionOptions(ctx, row, chunk);
  const replacesDraft =
    automaticReply &&
    (conversion.consumeDraft === true || matchingDraft(ctx, row, chunk));
  return {
    silent: row.disableNotification,
    ...conversion,
    ...(replacesDraft && row.replyToMessageId !== null
      ? { fallbackReplyToMessageId: row.replyToMessageId }
      : {}),
    ...(row.replyToMessageId === null || replacesDraft
      ? {}
      : { replyToMessageId: row.replyToMessageId }),
  };
}
