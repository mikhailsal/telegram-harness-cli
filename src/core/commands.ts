import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { readUpdateCache } from "./update.js";
import { catalog, transientIntent } from "../channels/index.js";
import type { ChannelEvent } from "../channels/index.js";
import type { Turn } from "../store/index.js";
import type { RuntimeContext } from "./context.js";
import { consumeTurnInbox } from "./queue.js";
import { CompactRejectedError } from "../adapters/contract.js";
import { decideFormat, showFormat } from "./format-command.js";
export interface CommandDelivery {
  finish(
    turn: Turn,
    status: "completed" | "failed" | "cancelled" | "interrupted",
    text: string,
    options?: { silent?: boolean; replyToMessageId?: number },
  ): boolean;
}
type Command = Extract<ChannelEvent, { kind: "command" }>;
export class Commands {
  constructor(privateContext: RuntimeContext, delivery: CommandDelivery) {
    this.ctx = privateContext;
    this.delivery = delivery;
  }
  private readonly ctx: RuntimeContext;
  private readonly delivery: CommandDelivery;
  async run(event: Command): Promise<void> {
    const strings = catalog[event.language];
    const active = this.ctx.store.listTurns([
      "submitting",
      "active",
      "uncertain",
    ]);
    if (event.command === "stop") {
      this.stop(active);
      return;
    }
    if (event.command === "new") {
      await this.newSession(active);
      return;
    }
    if (event.command === "mode") {
      await this.mode(event);
      return;
    }
    if (event.command === "compact") {
      await this.compact(event);
      return;
    }
    if (event.command === "format") {
      await showFormat(this.ctx, event);
      return;
    }
    if (event.command === "start") {
      await this.ctx.transient.send(
        transientIntent(
          `welcome/${String(event.updateId)}`,
          "welcome",
          event.chatId,
          this.ctx.now(),
        ),
        strings.welcome,
      );
      return;
    }
    if (event.command === "status") {
      await this.status(event, active);
      return;
    }
    if (event.command === "help") await this.reply(event, strings.welcome);
  }
  private async newSession(active: Turn[]): Promise<void> {
    if (active.length || !this.ctx.adapterReady) {
      this.ctx.store.setCursor("deferred-new", "1");
    } else {
      await this.ctx.rotateSession();
    }
  }
  private async compact(event: Command): Promise<void> {
    const compact = this.ctx.adapter.compact?.bind(this.ctx.adapter);
    if (
      !this.ctx.adapterReady ||
      !compact ||
      !this.ctx.adapter.capabilities.has("compact")
    ) {
      await this.reply(event, catalog[event.language].compactRejected);
      return;
    }
    const key = `compact-intent/${String(event.updateId)}`;
    if (this.ctx.store.getCursor(key)) return;
    this.ctx.store.setCursor(key, "1");
    try {
      await compact();
    } catch (error) {
      this.ctx.log("compact.failed", {
        code: error instanceof CompactRejectedError ? "rejected" : "uncertain",
      });
      await this.reply(
        event,
        error instanceof CompactRejectedError
          ? catalog[event.language].compactRejected
          : catalog[event.language].compactUncertain,
      );
    }
  }
  private async status(event: Command, active: Turn[]): Promise<void> {
    const strings = catalog[event.language];
    const mode =
      this.ctx.store.getCursor("followup-mode") ?? this.ctx.config.mode;
    const session = this.ctx.store.getSession(this.ctx.sessionId);
    const update = await this.availableUpdate();
    await this.reply(
      event,
      [
        `${strings.statusFolder}: ${this.ctx.directory}`,
        `${strings.statusHarness}: ${this.ctx.config.codex.adapter}`,
        `${strings.statusMode}: ${mode}`,
        active.length ? strings.working : strings.idle,
        session?.startedAt ?? "",
        update
          ? `${update}: npx telegram-harness-cli@latest init --update`
          : "",
      ]
        .filter(Boolean)
        .join("\n"),
    );
  }
  private async availableUpdate(): Promise<string | undefined> {
    if (!this.ctx.config.update_check) return;
    try {
      const cache = await readUpdateCache(this.ctx.directory);
      if (!cache?.updateAvailable || !cache.latestVersion) return;
      const version = (
        await readFile(join(this.ctx.directory, ".thc", "version"), "utf8")
      ).trim();
      return version === cache.currentVersion ? cache.latestVersion : undefined;
    } catch {
      return;
    }
  }
  private stop(active: Turn[]): void {
    for (const turn of this.ctx.store.listTurns(["queued"])) {
      if (turn.entityId !== this.ctx.entityId) continue;
      if (this.delivery.finish(turn, "cancelled", "", { silent: true }))
        consumeTurnInbox(this.ctx, turn.id);
    }
    for (const turn of active) {
      if (turn.entityId !== this.ctx.entityId) continue;
      this.ctx.store.setCursor(`user-stop/${turn.id}`, "1");
      this.ctx.store.setCursor("stop-pending", "1");
    }
  }
  private async mode(event: Command): Promise<void> {
    const mode = event.arguments.trim().toLowerCase();
    const strings = catalog[event.language];
    if (!mode) {
      await this.modeChooser(event);
      return;
    }
    if (mode === "queue" || mode === "steer")
      this.ctx.store.setCursor("followup-mode", mode);
    await this.reply(
      event,
      (mode ||
        (this.ctx.store.getCursor("followup-mode") ?? this.ctx.config.mode)) ===
        "steer"
        ? strings.steerMode
        : strings.queueMode,
    );
  }
  private async modeChooser(event: Command): Promise<void> {
    const strings = catalog[event.language];
    const intent = transientIntent(
      `command/${String(event.updateId)}`,
      "command",
      event.chatId,
      this.ctx.now(),
    );
    await this.ctx.transient.create(
      intent,
      async () =>
        (
          await this.ctx.channel.bot.api.sendMessage(
            event.chatId,
            strings.mode,
            {
              reply_markup: {
                inline_keyboard: [
                  [
                    { text: strings.chooseQueue, callback_data: "mode:queue" },
                    { text: strings.chooseSteer, callback_data: "mode:steer" },
                  ],
                ],
              },
            },
          )
        ).message_id,
    );
  }
  async decision(
    event: Extract<ChannelEvent, { kind: "decision" }>,
  ): Promise<boolean> {
    if (await decideFormat(this.ctx, event)) return true;
    const match = /^mode:(queue|steer)$/u.exec(event.data);
    if (!match?.[1]) return false;
    const card = this.ctx.store
      .listTransients()
      .find(
        (row) =>
          row.kind === "command" &&
          row.chatId === event.chatId &&
          row.messageId === event.messageId,
      );
    if (!card) {
      await this.ctx.channel.answerCallback(
        event.callbackId,
        catalog[event.language].stale,
      );
      return true;
    }
    this.ctx.store.setCursor("followup-mode", match[1]);
    await this.ctx.channel.answerCallback(event.callbackId);
    await this.ctx.transient.remove(card.id);
    return true;
  }
  private async reply(event: Command, text: string): Promise<void> {
    await this.ctx.transient.send(
      transientIntent(
        `command/${String(event.updateId)}`,
        "command",
        event.chatId,
        this.ctx.now(),
      ),
      text,
    );
  }
}
