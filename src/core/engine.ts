import { SteerRejectedError } from "../adapters/contract.js";
import { catalog, transientIntent } from "../channels/index.js";
import type { ChannelEvent } from "../channels/index.js";
import type { AdapterEvent } from "../adapters/contract.js";
import type { Turn } from "../store/index.js";
import type { RuntimeContext } from "./context.js";
import { Commands } from "./commands.js";
import { TurnStatuses } from "./status.js";
import type { CommandDelivery } from "./commands.js";
import { AdapterEvents, activeTurn, queueRequestSummaries } from "./events.js";
import {
  recordReaction,
  trackReaction,
  acceptReaction,
} from "./interactions.js";
import {
  collectMessages,
  consumeTurnInbox,
  decodeInbox,
  recordEvent,
  nextQueuedTurn,
  turnText,
} from "./queue.js";

const transientEvents = new Set<AdapterEvent["kind"]>([
  "approval-requested",
  "question-requested",
  "approval-resolved",
  "error",
  "progress",
]);
const requestEvents = new Set<AdapterEvent["kind"]>([
  "approval-requested",
  "question-requested",
]);
export interface EngineDelivery extends CommandDelivery {
  onDelivered: ((turnId: string) => Promise<void>) | undefined;
  flush(): Promise<void>;
}
export class TurnEngine {
  private readonly ctx: RuntimeContext;
  private readonly delivery: EngineDelivery;
  private readonly commands: Commands;
  private readonly events: AdapterEvents;
  private readonly statuses: TurnStatuses;
  private chain: Promise<void> = Promise.resolve();
  private recoveryDeadline: number | undefined;
  private readonly replayedRequests = new Set<string>();
  constructor(context: RuntimeContext, delivery: EngineDelivery) {
    this.ctx = context;
    this.statuses = new TurnStatuses(context);
    this.delivery = delivery;
    this.commands = new Commands(context, delivery);
    this.events = new AdapterEvents(context, delivery);
    delivery.onDelivered = async (turnId) => {
      await this.statuses.remove(turnId);
      await context.transient.removeKind("welcome");
    };
  }
  private serial(action: () => Promise<void>): Promise<void> {
    const result = this.chain.then(action);
    this.chain = result.catch(() => undefined);
    return result;
  }
  accept(events: ChannelEvent[], nextOffset: number): Promise<void> {
    return this.serial(async () => {
      for (const event of events) recordEvent(this.ctx, event);
      const previous = Number(
        this.ctx.store.getCursor("telegram-offset") ?? "0",
      );
      this.ctx.store.setCursor(
        "telegram-offset",
        String(Math.max(previous, nextOffset)),
      );
      await this.processInbox();
      await this.pump();
      this.statuses.refresh();
    });
  }
  recover(events: AdapterEvent[]): Promise<void> {
    return this.serial(async () => {
      this.replayedRequests.clear();
      for (const event of events) await this.apply(event);
      await this.events.repairFollowups();
      const state = this.ctx.adapter.pendingState
        ? await this.ctx.adapter.pendingState()
        : "unknown";
      if (state === "none") {
        this.recoveryDeadline = undefined;
        await this.events.pending.recover(this.replayedRequests);
        queueRequestSummaries(this.ctx);
      } else if (this.ctx.store.listRequests(["pending", "answering"]).length) {
        this.recoveryDeadline = this.ctx.now().getTime() + 30000;
      }
      for (const turn of this.ctx.store.listTurns(["submitting"])) {
        if (turn.entityId === this.ctx.entityId)
          this.ctx.store.transitionTurn(turn.id, "submitting", "uncertain", {
            updatedAt: this.ctx.now().toISOString(),
          });
      }
      await this.processInbox();
      await this.pump();
      await this.delivery.flush();
      this.statuses.refresh();
    });
  }
  event(event: AdapterEvent): Promise<void> {
    return this.serial(async () => {
      await this.apply(event);
      await this.pump();
      this.statuses.refresh();
    });
  }
  private async apply(event: AdapterEvent): Promise<void> {
    const session = this.ctx.store.getSession(this.ctx.sessionId);
    if (session && session.externalId !== event.sessionId) return;
    const durable = !transientEvents.has(event.kind);
    const key = `adapter-event/${event.sessionId}/${event.cursor}`;
    if (durable && this.ctx.store.getCursor(key)) return;
    await this.events.apply(event);
    if (requestEvents.has(event.kind) && event.requestId)
      this.replayedRequests.add(event.requestId);
    queueRequestSummaries(this.ctx);
    this.statuses.event(event);
    if (this.ctx.adapterReady) this.ctx.persistAdapter();
    if (durable) {
      this.ctx.store.setCursor(key, "1");
      this.ctx.store.setCursor("adapter-cursor", event.cursor);
    }
    await this.delivery.flush();
  }

  tick(): Promise<void> {
    return this.serial(async () => {
      if (
        this.ctx.adapterReady &&
        this.recoveryDeadline !== undefined &&
        this.ctx.now().getTime() >= this.recoveryDeadline
      ) {
        await this.events.pending.recover(this.replayedRequests);
        this.recoveryDeadline = undefined;
        queueRequestSummaries(this.ctx);
      }
      if (this.ctx.adapterReady) this.failExpiredUncertain();
      await this.ctx.transient.sweep(this.ctx.now());
      if (this.ctx.adapterReady) await this.events.pending.tick();
      queueRequestSummaries(this.ctx);
      await this.processInbox();
      await this.pump();
      await this.delivery.flush();
      this.statuses.refresh();
    });
  }
  private async processInbox(): Promise<void> {
    for (const row of this.ctx.store.listQueuedInbox()) {
      if (row.entityId !== this.ctx.entityId) continue;
      const event = decodeInbox(row);
      this.ctx.store.setCursor("chat-id", event.chatId);
      this.ctx.store.setCursor("user-id", event.userId);
      this.ctx.store.setCursor("language", event.language);
      if (event.kind === "message") {
        await this.ctx.transient.removeKind("command", event.chatId);
        if (await this.events.pending.textAnswer(event)) {
          this.ctx.store.consumeControl(event.updateId);
        } else collectMessages(this.ctx);
        continue;
      }
      if (event.kind === "command") {
        await this.processCommand(event);
      }
      if (event.kind === "decision") {
        if (!(await this.events.pending.decision(event)))
          await this.commands.decision(event);
        queueRequestSummaries(this.ctx);
      }
      if (event.kind === "reaction") {
        recordReaction(this.ctx, event);
        continue;
      }
      if (event.kind === "generation-stopped") {
        await this.stopActive();
        await this.statuses.cancelled();
      }
      this.ctx.store.consumeControl(event.updateId);
    }
    collectMessages(this.ctx);
  }
  private async processCommand(
    event: Extract<ChannelEvent, { kind: "command" }>,
  ): Promise<void> {
    await this.ctx.transient.removeKind("command", event.chatId);
    this.ctx.transient.registerExisting(
      transientIntent(
        `command-input/${String(event.updateId)}`,
        "command",
        event.chatId,
        this.ctx.now(),
      ),
      event.messageId,
    );
    if (event.command === "stop") await this.prepareStop();
    await this.commands.run(event);
    await this.statuses.cancelled();
  }
  private failExpiredUncertain(): void {
    for (const turn of this.ctx.store.listTurns(["uncertain"])) {
      if (turn.entityId !== this.ctx.entityId) continue;
      if (
        this.ctx.now().getTime() - Date.parse(turn.updatedAt) <
        this.ctx.config.submit_ack_timeout * 1000
      )
        continue;
      const language =
        this.ctx.store.getCursor("language") === "ru" ? "ru" : "en";
      const stopped = this.ctx.store.getCursor(`user-stop/${turn.id}`) === "1";
      if (
        this.delivery.finish(
          turn,
          stopped ? "cancelled" : "failed",
          stopped ? "" : catalog[language].inputFailed,
          stopped ? { silent: true } : {},
        )
      )
        consumeTurnInbox(this.ctx, turn.id);
    }
  }
  private async stopActive(): Promise<void> {
    const turn = activeTurn(this.ctx);
    if (turn) {
      this.ctx.store.setCursor(`user-stop/${turn.id}`, "1");
      this.ctx.store.setCursor("stop-pending", "1");
    }
    await this.statuses.cancelled();
  }
  private async prepareStop(): Promise<void> {
    for (const turn of this.ctx.store.listTurns([
      "submitting",
      "active",
      "uncertain",
    ]))
      if (turn.entityId === this.ctx.entityId)
        this.ctx.store.setCursor(`user-stop/${turn.id}`, "1");
    await this.statuses.cancelled();
  }
  private async pump(): Promise<void> {
    if (!this.ctx.adapterReady) {
      this.statuses.refresh();
      return;
    }
    if (await this.pendingSecretInterrupt()) return;
    const active = activeTurn(this.ctx);
    if (
      active?.externalTurnId &&
      this.ctx.store.getCursor("stop-pending") === "1"
    ) {
      await this.ctx.adapter.interrupt(active.externalTurnId);
      this.ctx.store.setCursor("stop-pending", "0");
      return;
    }
    if (active) {
      await this.steer(active);
      return;
    }
    if (this.ctx.store.getCursor("stop-pending") === "1")
      this.ctx.store.setCursor("stop-pending", "0");
    if (this.ctx.store.getCursor("deferred-new") === "1") {
      await this.ctx.rotateSession();
      this.ctx.store.setCursor("deferred-new", "0");
    }
    const turn = nextQueuedTurn(this.ctx);
    if (turn) {
      await this.submit(turn);
      const started = activeTurn(this.ctx);
      if (started) await this.steer(started);
    }
  }
  private async pendingSecretInterrupt(): Promise<boolean> {
    for (const turn of this.ctx.store.listTurns(["failed"])) {
      const key = `secret-interrupt/${turn.id}`;
      const externalId = this.ctx.store.getCursor(key);
      if (!externalId) continue;
      try {
        await this.ctx.adapter.interrupt(externalId);
        this.ctx.store.setCursor(key, "");
      } catch {
        this.ctx.log("secret_interrupt_failed", { code: "unconfirmed" });
        return true;
      }
    }
    return false;
  }
  private async steer(active: Turn): Promise<void> {
    const mode =
      this.ctx.store.getCursor("followup-mode") ?? this.ctx.config.mode;
    if (
      mode !== "steer" ||
      !active.externalTurnId ||
      !this.ctx.adapter.steer ||
      !this.ctx.adapter.capabilities.has("steer")
    )
      return;
    const turn = nextQueuedTurn(this.ctx);
    if (!turn) return;
    trackReaction(this.ctx, turn.id);
    const text = await turnText(this.ctx, turn);
    this.ctx.store.setCursor(`followup-of/${turn.id}`, active.id);
    if (
      !this.ctx.store.transitionTurn(turn.id, "queued", "submitting", {
        sessionId: this.ctx.sessionId,
        updatedAt: this.ctx.now().toISOString(),
      })
    )
      return;
    try {
      await this.ctx.adapter.steer(
        { correlationId: turn.id, text },
        active.externalTurnId,
      );
      this.ctx.store.transitionTurn(turn.id, "submitting", "active", {
        externalTurnId: active.externalTurnId,
        updatedAt: this.ctx.now().toISOString(),
      });
      consumeTurnInbox(this.ctx, turn.id);
      acceptReaction(this.ctx, turn.id);
      this.statuses.acknowledge();
    } catch (error) {
      if (error instanceof SteerRejectedError) {
        this.ctx.store.requeueRejectedSteer(
          turn.id,
          this.ctx.now().toISOString(),
        );
        return;
      }
      this.ctx.store.transitionTurn(turn.id, "submitting", "uncertain", {
        updatedAt: this.ctx.now().toISOString(),
      });
    }
  }
  private async submit(turn: Turn): Promise<void> {
    trackReaction(this.ctx, turn.id);
    const text = await turnText(this.ctx, turn);
    this.ctx.store.setCursor(`followup-of/${turn.id}`, "");
    if (
      !this.ctx.store.transitionTurn(turn.id, "queued", "submitting", {
        sessionId: this.ctx.sessionId,
        updatedAt: this.ctx.now().toISOString(),
      })
    )
      return;
    this.ctx.persistAdapter();
    this.statuses.refresh();
    try {
      const result = await this.ctx.adapter.submit({
        correlationId: turn.id,
        text,
      });
      this.ctx.persistAdapter();
      if (result.turnId)
        this.ctx.store.transitionTurn(turn.id, "submitting", "active", {
          externalTurnId: result.turnId,
          updatedAt: this.ctx.now().toISOString(),
        });
      consumeTurnInbox(this.ctx, turn.id);
      acceptReaction(this.ctx, turn.id);
    } catch {
      this.ctx.log("submit_uncertain", { code: "unconfirmed" });
      this.ctx.store.transitionTurn(turn.id, "submitting", "uncertain", {
        updatedAt: this.ctx.now().toISOString(),
      });
    }
  }
  close(): Promise<void> {
    return this.serial(async () => {
      await this.statuses.close();
    });
  }
}
