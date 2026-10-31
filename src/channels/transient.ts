import { GrammyError } from "grammy";
import type { Store, TransientInput } from "../store/index.js";
import type { TelegramChannel } from "./telegram.js";

export function transientIntent(
  id: string,
  kind: TransientInput["kind"],
  chatId: string,
  now: Date,
): TransientInput {
  const duration =
    kind === "welcome"
      ? 47 * 60 * 60 * 1000
      : kind === "command"
        ? 10 * 60 * 1000
        : null;
  return {
    id,
    kind,
    chatId,
    createdAt: now.toISOString(),
    expiresAt:
      duration === null
        ? null
        : new Date(now.getTime() + duration).toISOString(),
  };
}
export class TransientManager {
  private readonly channel: TelegramChannel;
  private readonly store: Store;
  private readonly now: () => Date;
  constructor(
    channel: TelegramChannel,
    store: Store,
    now: () => Date = () => new Date(),
  ) {
    this.channel = channel;
    this.store = store;
    this.now = now;
  }
  registerExisting(intent: TransientInput, messageId: number): void {
    if (this.store.putTransient(intent))
      this.store.setTransientMessage(intent.id, messageId, intent.createdAt);
  }
  async removeKind(
    kind: TransientInput["kind"],
    chatId?: string,
  ): Promise<void> {
    for (const row of this.store.listTransients())
      if (row.kind === kind && (chatId === undefined || row.chatId === chatId))
        await this.remove(row.id);
  }
  async send(
    intent: TransientInput,
    text: string,
  ): Promise<number | undefined> {
    return this.create(
      intent,
      async () =>
        (
          await this.channel.bot.api.sendMessage(intent.chatId, text, {
            disable_notification:
              intent.kind !== "welcome" &&
              intent.kind !== "approval" &&
              intent.kind !== "question",
          })
        ).message_id,
    );
  }
  async create(
    intent: TransientInput,
    send: () => Promise<number>,
  ): Promise<number | undefined> {
    const key = `transient-retry/${intent.id}`;
    if (!this.store.putTransient(intent)) {
      const row = this.store
        .listTransients()
        .find((item) => item.id === intent.id);
      if (row?.messageId != null) return row.messageId;
      const retryAt = this.store.getCursor(key);
      if (!retryAt || retryAt > this.now().toISOString()) return undefined;
    }
    // Clear before the external side effect: a crash here is conservatively ambiguous.
    this.store.setCursor(key, "");
    try {
      const messageId = await send();
      if (
        !this.store.setTransientMessage(
          intent.id,
          messageId,
          this.now().toISOString(),
        )
      ) {
        const current = this.store
          .listTransients()
          .find((row) => row.id === intent.id);
        if (current?.messageId !== messageId)
          await this.deleteMessage(intent.chatId, messageId);
        return current?.messageId ?? undefined;
      }
      return messageId;
    } catch (error) {
      this.recordRejection(key, error);
      throw error;
    }
  }
  private recordRejection(key: string, error: unknown): void {
    if (
      !(error instanceof GrammyError) ||
      error.error_code < 400 ||
      error.error_code >= 500 ||
      error.error_code === 408
    )
      return;
    const seconds =
      error.error_code === 429 ? (error.parameters.retry_after ?? 1) : 1;
    this.store.setCursor(
      key,
      new Date(this.now().getTime() + seconds * 1000).toISOString(),
    );
  }
  async repost(
    id: string,
    now: Date,
    sendSilent: () => Promise<number>,
  ): Promise<boolean> {
    await this.cleanupReposts(id);
    const row = this.store.listTransients().find((item) => item.id === id);
    if (
      row?.messageId == null ||
      !row.sentAt ||
      now.getTime() - Date.parse(row.sentAt) < 47 * 60 * 60 * 1000
    )
      return false;
    const intent: TransientInput = {
      id: `${id}/repost/${String(row.messageId)}`,
      kind: row.kind,
      chatId: row.chatId,
      createdAt: now.toISOString(),
      expiresAt: null,
    };
    const nextId = await this.create(intent, sendSilent);
    if (nextId === undefined) return false;
    if (
      !this.store.replaceTransientMessage(
        id,
        row.messageId,
        nextId,
        now.toISOString(),
        row.expiresAt,
      )
    )
      return false;
    await this.cleanupReposts(id);
    return true;
  }
  private async cleanupReposts(id: string): Promise<void> {
    const rows = this.store.listTransients();
    const current = rows.find((row) => row.id === id);
    for (const replacement of rows) {
      const prefix = `${id}/repost/`;
      if (
        !replacement.id.startsWith(prefix) ||
        replacement.messageId === null ||
        replacement.messageId !== current?.messageId
      )
        continue;
      const oldId = Number(replacement.id.slice(prefix.length));
      if (Number.isSafeInteger(oldId))
        await this.deleteMessage(replacement.chatId, oldId);
      this.store.deleteTransient(replacement.id);
    }
  }
  private async deleteMessage(
    chatId: string,
    messageId: number,
  ): Promise<void> {
    try {
      await this.channel.delete(chatId, messageId);
    } catch {
      try {
        await this.channel.bot.api.editMessageReplyMarkup(chatId, messageId, {
          reply_markup: { inline_keyboard: [] },
        });
      } catch {
        /* deletion is best effort */
      }
    }
  }
  async remove(id: string): Promise<void> {
    await this.cleanupReposts(id);
    const rows = this.store.listTransients();
    const row = rows.find((item) => item.id === id);
    if (!row) return;
    for (const replacement of rows.filter((item) =>
      item.id.startsWith(`${id}/repost/`),
    )) {
      if (
        replacement.messageId !== null &&
        replacement.messageId !== row.messageId
      )
        await this.deleteMessage(replacement.chatId, replacement.messageId);
      this.store.deleteTransient(replacement.id);
      this.store.setCursor(`transient-retry/${replacement.id}`, "");
    }
    if (row.messageId !== null)
      await this.deleteMessage(row.chatId, row.messageId);
    this.store.deleteTransient(id);
    this.store.setCursor(`transient-retry/${id}`, "");
  }
  async sweep(now: Date): Promise<void> {
    for (const row of this.store.listTransients())
      if (row.expiresAt && row.expiresAt <= now.toISOString())
        await this.remove(row.id);
  }
}
