import { randomUUID } from "node:crypto";
import { z } from "zod";
import {
  catalog,
  classifyTelegramError,
  renderOutgoing,
} from "../channels/index.js";
import type { RenderChunk, SendOptions } from "../channels/index.js";
import type { Outbox, OutboxInput, Turn } from "../store/index.js";
import type { RuntimeContext } from "./context.js";
import { replyTarget } from "./reply-target.js";
import { turnDraftId } from "./turn-draft.js";
import { messageFormat } from "./format.js";

const chunkSchema = z.strictObject({
  kind: z.enum(["message", "file", "photo", "document"]),
  text: z.string(),
  plain: z.string(),
  author: z.enum(["assistant", "system", "user"]).optional(),
  automaticReply: z.boolean().optional(),
  style: z.literal("html").optional(),
  filePath: z.string().optional(),
  fileName: z.string().optional(),
  digest: z.string().optional(),
  workspace: z.string().optional(),
});
type Chunk = z.infer<typeof chunkSchema>;
type TerminalStatus = "completed" | "failed" | "cancelled" | "interrupted";
interface FinishOptions {
  silent?: boolean;
  replyToMessageId?: number;
  automaticReply?: boolean;
}
function fallbackText(context: RuntimeContext, status: TerminalStatus): string {
  const { config, store } = context;
  const language =
    config.language === "auto"
      ? store.getCursor("language") === "ru"
        ? "ru"
        : "en"
      : config.language;
  if (status === "failed" || status === "interrupted")
    return catalog[language][status];
  return catalog[language].empty;
}

export class Delivery {
  private readonly context: RuntimeContext;
  private sending = false;
  onSending: ((turnId: string) => Promise<boolean> | Promise<void>) | undefined;
  onSendFailed: ((turnId: string) => void) | undefined;
  onDelivered: ((turnId: string) => Promise<void>) | undefined;
  constructor(context: RuntimeContext) {
    this.context = context;
  }
  finish(
    turn: Turn,
    status: TerminalStatus,
    text: string,
    options: FinishOptions = {},
  ): boolean {
    const { config, store } = this.context;
    const chatId = this.chatId(turn.id);
    const format = messageFormat(this.context, turn, chatId);
    let rendered = renderOutgoing(
      text,
      config.markup.tags.map((tag) => tag.name),
      this.context.directory,
      format,
    );
    if (rendered.unclosedTag) {
      store.setCursor(
        "markup-warnings",
        String(Number(store.getCursor("markup-warnings") ?? 0) + 1),
      );
      this.context.log("markup.unclosed", { turn: turn.id });
    }
    const fallback = fallbackText(this.context, status);
    const marker = config.non_tg_marker;
    if (turn.source !== "telegram" && rendered.visibleText.trim() === marker)
      rendered = renderOutgoing(`${marker} ${fallback}`, [], undefined, format);
    const chunks = options.silent
      ? []
      : rendered.chunks.length
        ? rendered.chunks
        : [
            {
              kind: "message" as const,
              text: fallback,
              plain: fallback,
              ...(format === "regular" ? { style: "html" as const } : {}),
            },
          ];
    const result = options.silent ? "" : rendered.visibleText || fallback;
    const rows = this.rows(
      turn,
      chunks,
      chatId,
      this.replyOptions(turn, chatId, options),
    );
    return store.finishTurnAndEnqueue(
      turn.id,
      turn.status,
      status,
      { result, updatedAt: this.context.now().toISOString() },
      rows,
    );
  }
  private replyOptions(
    turn: Turn,
    chatId: string,
    options: FinishOptions,
  ): FinishOptions {
    const replyToMessageId =
      options.replyToMessageId ?? replyTarget(this.context.store, turn, chatId);
    return {
      ...options,
      ...(options.replyToMessageId === undefined &&
      replyToMessageId !== undefined
        ? { automaticReply: true }
        : {}),
      ...(replyToMessageId === undefined ? {} : { replyToMessageId }),
    };
  }
  private rows(
    turn: Turn,
    chunks: RenderChunk[],
    chatId: string,
    options: FinishOptions,
  ): OutboxInput[] {
    return chunks.map((chunk, index) => ({
      id: `${turn.id}/${String(index)}`,
      turnId: turn.id,
      chunk: index,
      chatId,
      text: chunk.text,
      payload: JSON.stringify({
        ...chunk,
        ...(index === 0 && options.automaticReply
          ? { automaticReply: true }
          : {}),
      }),
      format:
        chunk.kind === "message"
          ? chunk.style === "html"
            ? "html"
            : "rich"
          : "document",
      createdAt: this.context.now().toISOString(),
      disableNotification: index > 0 || chunk.kind !== "message",
      ...(options.replyToMessageId === undefined || index > 0
        ? {}
        : { replyToMessageId: options.replyToMessageId }),
    }));
  }
  private chatId(turnId: string): string {
    const message = this.context.store
      .messagesForTurn(turnId)
      .filter((row) => row.author === "user")
      .at(-1);
    return (
      message?.chatId ??
      this.context.store.getCursor("chat-id") ??
      String(this.context.config.allowlist[0])
    );
  }
  async flush(): Promise<void> {
    if (this.sending) return;
    this.sending = true;
    try {
      const rows = this.context.store.listOutbox([
        "pending",
        "sending",
        "failed",
      ]);
      const turns = new Map<string, Outbox[]>();
      for (const row of rows) {
        const group = turns.get(row.turnId) ?? [];
        group.push(row);
        turns.set(row.turnId, group);
      }
      for (const group of turns.values()) {
        await this.flushTurn(group);
      }
    } finally {
      this.sending = false;
    }
  }
  private async flushTurn(group: Outbox[]): Promise<void> {
    group.sort((a, b) =>
      a.chunk < 0 && b.chunk < 0 ? b.chunk - a.chunk : a.chunk - b.chunk,
    );
    let blocked = false;
    for (const row of group) {
      if (row.chunk >= 0 && blocked) continue;
      if (row.status === "pending") await this.send(row);
      if (
        row.chunk >= 0 &&
        this.context.store.getOutbox(row.id)?.status !== "sent"
      )
        blocked = true;
    }
  }
  private async send(row: Outbox): Promise<void> {
    const { store, channel } = this.context;
    if (
      row.nextAttemptAt &&
      row.nextAttemptAt > this.context.now().toISOString()
    )
      return;
    if (row.chunk >= 0 && (await this.onSending?.(row.turnId)) === false)
      return;
    if (!store.beginSend(row.id, this.context.now().toISOString())) return;
    const chunk = this.prepareChunk(row);
    if (!chunk) return;
    const { automaticReply, ...renderedChunk } = chunk;
    let delivered: Awaited<ReturnType<typeof channel.deliverChunk>>;
    try {
      delivered = await channel.deliverChunk(
        row.chatId,
        renderedChunk,
        this.sendOptions(row, chunk, automaticReply),
      );
      if (delivered.messageIds[0] === undefined) {
        store.markFailed(row.id, "missing-message-id");
        this.notifyFailure(row);
        return;
      }
    } catch (error) {
      this.failed(row, error);
      return;
    }
    await this.record(
      row,
      chunk,
      delivered.messageIds[0],
      delivered.visibleText,
    );
  }
  private prepareChunk(row: Outbox): Chunk | undefined {
    try {
      const chunk = chunkSchema.parse(
        JSON.parse(row.payload ?? "null") as unknown,
      );
      if (this.validWorkspace(chunk)) return chunk;
    } catch {
      // Invalid JSON or payload has the same permanent failure as a bad workspace.
    }
    this.context.store.markFailed(row.id, "invalid-payload");
    this.notifyFailure(row);
    return undefined;
  }
  private validWorkspace(chunk: Chunk): boolean {
    return (
      (chunk.kind !== "photo" && chunk.kind !== "document") ||
      chunk.workspace === this.context.directory
    );
  }
  private sendOptions(
    row: Outbox,
    chunk: Chunk,
    automaticReply: boolean | undefined,
  ): SendOptions {
    const turn = automaticReply
      ? this.context.store.getTurn(row.turnId)
      : undefined;
    const replacesDraft =
      turn &&
      chunk.kind === "message" &&
      this.context.channel.hasDraft(
        row.chatId,
        turnDraftId(turn),
        chunk.style === "html" ? "regular" : "rich",
      );
    return {
      silent: row.disableNotification,
      ...(replacesDraft && row.replyToMessageId !== null
        ? { fallbackReplyToMessageId: row.replyToMessageId }
        : {}),
      ...(row.replyToMessageId === null || replacesDraft
        ? {}
        : { replyToMessageId: row.replyToMessageId }),
    };
  }
  private async record(
    row: Outbox,
    chunk: Chunk,
    messageId: number,
    text: string,
  ): Promise<void> {
    const { store } = this.context;
    const sentAt = this.context.now().toISOString();
    const marked = store.markSent(row.id, messageId, {
      id: randomUUID(),
      chatId: row.chatId,
      telegramMessageId: messageId,
      author: chunk.author ?? "assistant",
      sentAt,
      text,
      turnId: row.turnId,
      possiblyDuplicated: row.possiblyDuplicated,
    });
    if (
      !marked ||
      row.chunk < 0 ||
      (chunk.author !== undefined && chunk.author !== "assistant")
    )
      return;
    const turn = store.getTurn(row.turnId);
    if (
      !turn ||
      !["completed", "failed", "interrupted", "cancelled"].includes(turn.status)
    )
      return;
    if (!store.getCursor("first-delivery-at"))
      store.setCursor("first-delivery-at", sentAt);
    if (
      !store
        .listOutbox(["pending", "sending", "failed"])
        .some((part) => part.turnId === row.turnId && part.chunk >= 0)
    )
      await this.onDelivered?.(row.turnId);
  }
  private notifyFailure(row: Outbox): void {
    if (row.chunk >= 0) this.onSendFailed?.(row.turnId);
  }
  private failed(row: Outbox, error: unknown): void {
    const result = classifyTelegramError(error);
    if (["network", "server", "rate-limit"].includes(result.kind)) {
      const seconds =
        result.retryAfter ?? Math.min(60, 2 ** Math.min(row.attempts, 6));
      this.context.store.scheduleRetry(
        row.id,
        result.kind,
        new Date(this.context.now().getTime() + seconds * 1000).toISOString(),
      );
    } else this.context.store.markFailed(row.id, result.kind);
    this.notifyFailure(row);
    this.context.log("delivery.failed", { kind: result.kind });
  }
}
