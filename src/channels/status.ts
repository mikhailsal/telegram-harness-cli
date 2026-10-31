import { classifyTelegramError } from "./telegram.js";
import type { TelegramChannel } from "./telegram.js";
import type { TransientManager } from "./transient.js";

export class ChannelStatus {
  private readonly channel: TelegramChannel;
  private readonly chatId: number;
  private readonly draftId: number;
  private readonly transient: TransientManager | undefined;
  private readonly transientId: string | undefined;
  private readonly now: () => Date;
  private timer: ReturnType<typeof setTimeout> | undefined;
  private heartbeat: ReturnType<typeof setInterval> | undefined;
  private text = "";
  private last = 0;
  private messageId: number | undefined;
  private draftEnabled = true;
  private busy = false;
  private pending: Promise<void> | undefined;
  private revision = 0;
  private stopped = false;
  private retryAt = 0;
  private repostCheckAt = 0;
  constructor(
    channel: TelegramChannel,
    chatId: number,
    draftId: number,
    transient?: TransientManager,
    transientId?: string,
    mode: "draft" | "message" = "draft",
    now: () => Date = () => new Date(),
  ) {
    this.channel = channel;
    this.chatId = chatId;
    this.draftId = draftId;
    this.transient = transient;
    this.transientId = transientId;
    this.draftEnabled = mode === "draft";
    this.now = now;
  }
  update(text: string, force = false): void {
    if (
      !force &&
      this.text === text &&
      !this.stopped &&
      this.heartbeat &&
      this.retryAt <= this.now().getTime()
    )
      return;
    this.text = text;
    this.revision++;
    this.stopped = false;
    this.heartbeat ??= setInterval(() => {
      void this.flush();
    }, 20_000);
    this.timer ??= setTimeout(
      () => {
        this.timer = undefined;
        void this.flush();
      },
      Math.max(
        0,
        1000 - (this.now().getTime() - this.last),
        this.retryAt - this.now().getTime(),
      ),
    );
  }
  private async flush(): Promise<void> {
    if (this.stopped || this.busy) return;
    if (this.now().getTime() < this.retryAt) {
      this.update(this.text, true);
      return;
    }
    this.busy = true;
    const revision = this.revision;
    this.pending = this.sendStatus();
    try {
      await this.pending;
    } finally {
      this.pending = undefined;
      this.busy = false;
      this.scheduleChanged(revision);
    }
  }
  private scheduleChanged(revision: number): void {
    if (!this.stopped && this.revision !== revision && !this.timer)
      this.update(this.text, true);
  }
  private async sendStatus(): Promise<void> {
    this.last = this.now().getTime();
    if (this.draftEnabled) {
      try {
        await this.channel.draft(this.chatId, this.draftId, this.text);
        return;
      } catch (error) {
        if (this.retry(error)) return;
        this.draftEnabled = false;
      }
    }
    try {
      await this.repostIfNeeded();
      if (this.messageId !== undefined)
        await this.channel.edit(String(this.chatId), this.messageId, this.text);
      else if (this.transient && this.transientId)
        await this.createTransient(this.transient, this.transientId);
      else
        this.messageId = (
          await this.channel.bot.api.sendMessage(this.chatId, this.text, {
            disable_notification: true,
          })
        ).message_id;
      await this.channel.typing(String(this.chatId));
    } catch (error) {
      this.retry(error);
    }
  }
  private async createTransient(
    transient: TransientManager,
    id: string,
  ): Promise<void> {
    const outcome = { sent: false };
    this.messageId = await transient.create(
      {
        id,
        kind: "status",
        chatId: String(this.chatId),
        createdAt: this.now().toISOString(),
        expiresAt: null,
      },
      async () => {
        const message = await this.channel.bot.api.sendMessage(
          this.chatId,
          this.text,
          { disable_notification: true },
        );
        outcome.sent = true;
        return message.message_id;
      },
    );
    if (!outcome.sent && this.messageId !== undefined) {
      const current = await this.repostIfNeeded();
      if (current !== undefined)
        await this.channel.edit(String(this.chatId), current, this.text);
    }
  }
  private async repostIfNeeded(): Promise<number | undefined> {
    if (!this.transient || !this.transientId || this.messageId === undefined)
      return;
    const now = this.now();
    if (now.getTime() < this.repostCheckAt) return this.messageId;
    this.repostCheckAt = now.getTime() + 60_000;
    const send = async (): Promise<number> =>
      (
        await this.channel.bot.api.sendMessage(this.chatId, this.text, {
          disable_notification: true,
        })
      ).message_id;
    if (!(await this.transient.repost(this.transientId, now, send)))
      return this.messageId;
    this.messageId = await this.transient.create(
      {
        id: this.transientId,
        kind: "status",
        chatId: String(this.chatId),
        createdAt: now.toISOString(),
        expiresAt: null,
      },
      send,
    );
    return this.messageId;
  }
  private retry(error: unknown): boolean {
    const result = classifyTelegramError(error);
    if (this.stopped || result.kind !== "rate-limit") return false;
    this.retryAt = this.now().getTime() + (result.retryAfter ?? 1) * 1000;
    this.update(this.text, true);
    return true;
  }
  async stop(): Promise<void> {
    this.stopped = true;
    if (this.timer) clearTimeout(this.timer);
    if (this.heartbeat) clearInterval(this.heartbeat);
    this.timer = undefined;
    this.heartbeat = undefined;
    await this.pending;
    if (this.transient && this.transientId)
      await this.transient.remove(this.transientId);
    else if (this.messageId !== undefined) {
      try {
        await this.channel.delete(String(this.chatId), this.messageId);
      } catch {
        /* best effort */
      }
    }
    this.messageId = undefined;
  }
}
