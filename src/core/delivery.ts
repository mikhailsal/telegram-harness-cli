import { randomUUID } from "node:crypto";
import { z } from "zod";
import {
  catalog,
  classifyTelegramError,
  renderOutgoing,
} from "../channels/index.js";
import type { RenderChunk } from "../channels/index.js";
import type { Outbox, OutboxInput, Turn } from "../store/index.js";
import type { RuntimeContext } from "./context.js";
import { replyTarget } from "./reply-target.js";

const chunkSchema = z.strictObject({
  kind: z.enum(["message", "file"]),
  text: z.string(),
  plain: z.string(),
  author: z.enum(["assistant", "system", "user"]).optional(),
});
type Chunk = z.infer<typeof chunkSchema>;
type TerminalStatus = "completed" | "failed" | "cancelled" | "interrupted";
interface FinishOptions {
  silent?: boolean;
  replyToMessageId?: number;
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
  onSending: ((turnId: string) => Promise<void>) | undefined;
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
    let rendered = renderOutgoing(
      text,
      config.markup.tags.map((tag) => tag.name),
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
      rendered = renderOutgoing(`${marker} ${fallback}`, []);
    const chunks = options.silent
      ? []
      : rendered.chunks.length
        ? rendered.chunks
        : [{ kind: "message" as const, text: fallback, plain: fallback }];
    const result = options.silent ? "" : rendered.visibleText || fallback;
    const chatId = this.chatId(turn.id);
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
      payload: JSON.stringify(chunk),
      format: chunk.kind === "file" ? "document" : "rich",
      createdAt: this.context.now().toISOString(),
      disableNotification: index > 0 || chunk.kind === "file",
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
    if (row.chunk >= 0) await this.onSending?.(row.turnId);
    if (!store.beginSend(row.id, this.context.now().toISOString())) return;
    let chunk: Chunk;
    try {
      chunk = chunkSchema.parse(JSON.parse(row.payload ?? "null") as unknown);
    } catch {
      store.markFailed(row.id, "invalid-payload");
      this.notifyFailure(row);
      return;
    }
    let delivered: Awaited<ReturnType<typeof channel.deliverChunk>>;
    try {
      delivered = await channel.deliverChunk(row.chatId, chunk, {
        silent: row.disableNotification,
        ...(row.replyToMessageId === null
          ? {}
          : { replyToMessageId: row.replyToMessageId }),
      });
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
