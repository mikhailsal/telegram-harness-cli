import { randomUUID } from "node:crypto";
import { catalog, renderOutgoing } from "../channels/index.js";
import type { AdapterEvent } from "../adapters/contract.js";
import type { Turn } from "../store/index.js";
import type { RuntimeContext } from "./context.js";
import type { CommandDelivery } from "./commands.js";
import { consumeTurnInbox } from "./queue.js";
import { PendingManager } from "./pending.js";
import { completionStatus, completionText } from "./results.js";
import { acceptReaction } from "./interactions.js";
import { messageFormat } from "./format.js";
import { isCompactionOnly, observeCompaction } from "./compaction.js";
export { queueRequestSummaries } from "./request-summary.js";

export function activeTurn(
  ctx: RuntimeContext,
  externalId?: string,
): Turn | undefined {
  const turns = ctx.store
    .listTurns(["submitting", "active", "uncertain"])
    .filter((turn) => turn.entityId === ctx.entityId);
  return externalId
    ? turns.find((turn) => turn.externalTurnId === externalId)
    : turns[0];
}
export class AdapterEvents {
  private readonly ctx: RuntimeContext;
  private readonly delivery: CommandDelivery;
  readonly pending: PendingManager;
  constructor(ctx: RuntimeContext, delivery: CommandDelivery) {
    this.ctx = ctx;
    this.delivery = delivery;
    this.pending = new PendingManager(ctx);
  }
  async apply(event: AdapterEvent): Promise<void> {
    if (event.kind === "approval-resolved") {
      await this.pending.resolved(event);
      return;
    }
    if (event.kind === "error") {
      this.ctx.log("adapter_disconnected");
      return;
    }
    const turn = this.findTurn(event);
    if (!turn) return;
    observeCompaction(this.ctx, turn, event);
    if (
      event.kind === "approval-requested" ||
      event.kind === "question-requested"
    ) {
      await this.approval(event, turn);
      return;
    }
    if (event.kind === "input") {
      this.input(event, turn);
      return;
    }
    if (event.kind === "output") {
      if (event.text !== undefined)
        this.ctx.store.setCursor(`turn-output/${turn.id}`, event.text);
      return;
    }
    if (event.kind === "turn-completed") await this.completedEvent(event, turn);
  }
  private async completedEvent(event: AdapterEvent, turn: Turn): Promise<void> {
    const key = `secret-interrupt/${turn.id}`;
    if (event.turnId === this.ctx.store.getCursor(key))
      this.ctx.store.setCursor(key, "");
    await this.complete(event, turn);
  }

  private findTurn(event: AdapterEvent): Turn | undefined {
    if (event.correlationId) {
      const own = this.ctx.store.getTurn(event.correlationId);
      if (own?.entityId === this.ctx.entityId) return own;
    }
    if (!event.turnId) return this.findWithoutTurnId(event);
    const existing = this.findExternal(event.turnId);
    if (existing) return existing;
    if (this.deferUnbound(event)) return;
    if (
      event.kind !== "turn-started" &&
      event.kind !== "input" &&
      event.kind !== "turn-completed"
    )
      return;
    return this.externalTurn(event.turnId);
  }
  private findWithoutTurnId(event: AdapterEvent): Turn | undefined {
    if (event.kind === "input" && !event.correlationId) {
      const unbound = this.ctx.store.getCursor("unbound-turn");
      if (unbound) return this.externalTurn(unbound);
    }
    return activeTurn(this.ctx);
  }
  private deferUnbound(event: AdapterEvent): boolean {
    if (event.kind !== "turn-started" || !event.turnId) return false;
    const unresolved = this.ctx.store
      .listTurns(["submitting", "active", "uncertain"])
      .some(
        (turn) => turn.entityId === this.ctx.entityId && !turn.externalTurnId,
      );
    if (unresolved) this.ctx.store.setCursor("unbound-turn", event.turnId);
    return unresolved;
  }
  private findExternal(externalId: string): Turn | undefined {
    const candidates = this.ctx.store
      .listTurns([
        "submitting",
        "active",
        "uncertain",
        "completed",
        "failed",
        "cancelled",
        "interrupted",
      ])
      .filter(
        (turn) =>
          turn.entityId === this.ctx.entityId &&
          turn.sessionId === this.ctx.sessionId &&
          turn.externalTurnId === externalId,
      );
    return (
      candidates.find(
        (turn) => !this.ctx.store.getCursor(`followup-of/${turn.id}`),
      ) ?? candidates[0]
    );
  }
  private externalTurn(externalTurnId: string): Turn | undefined {
    this.ctx.store.setCursor("unbound-turn", "");
    const found = activeTurn(this.ctx, externalTurnId);
    if (found) return found;
    const id = randomUUID();
    const now = this.ctx.now().toISOString();
    this.ctx.store.createTurn({
      id,
      entityId: this.ctx.entityId,
      source: "terminal",
      createdAt: now,
    });
    this.ctx.store.transitionTurn(id, "queued", "active", {
      sessionId: this.ctx.sessionId,
      externalTurnId,
      updatedAt: now,
    });
    const turn = this.ctx.store.getTurn(id);
    if (turn) messageFormat(this.ctx, turn);
    return turn;
  }
  private input(event: AdapterEvent, turn: Turn): void {
    acceptReaction(this.ctx, turn.id);
    const external = event.turnId ?? this.ctx.store.getCursor("unbound-turn");
    if (external) this.ctx.store.setCursor("unbound-turn", "");
    if (
      turn.source === "terminal" &&
      this.ctx.config.mirror_non_tg_input &&
      event.text
    ) {
      this.mirrorInput(event, turn);
    }
    if (turn.status === "submitting" || turn.status === "uncertain")
      this.ctx.store.transitionTurn(turn.id, turn.status, "active", {
        ...(external ? { externalTurnId: external } : {}),
        updatedAt: this.ctx.now().toISOString(),
      });
  }
  private mirrorInput(event: AdapterEvent, turn: Turn): void {
    const marker = `mirror/${turn.id}/${event.cursor}`;
    if (this.ctx.store.getCursor(marker)) return;
    const chat = this.ctx.store.getCursor("chat-id");
    if (!chat || !event.text) return;
    const text = `${this.ctx.config.non_tg_marker} ${event.text}`;
    const chunks = renderOutgoing(
      text,
      this.ctx.config.markup.tags.map((tag) => tag.name),
    ).chunks;
    let index = Math.min(
      0,
      ...this.ctx.store
        .listOutbox(["pending", "sending", "sent", "failed"])
        .filter((row) => row.turnId === turn.id)
        .map((row) => row.chunk),
    );
    for (const [part, chunk] of chunks.entries()) {
      index--;
      this.ctx.store.enqueueOutbox({
        id: `${marker}/${String(part)}`,
        turnId: turn.id,
        chunk: index,
        chatId: chat,
        text: chunk.text,
        createdAt: this.ctx.now().toISOString(),
        format: chunk.kind === "file" ? "document" : "rich",
        payload: JSON.stringify({ ...chunk, author: "user" }),
        disableNotification: true,
      });
    }
    this.ctx.store.setCursor(marker, "1");
  }
  private async approval(event: AdapterEvent, turn: Turn): Promise<void> {
    const inbox = this.ctx.store
      .listInbox(["assigned", "consumed"])
      .find((row) => row.turnId === turn.id);
    const chat = inbox?.chatId ?? this.ctx.store.getCursor("chat-id");
    const user = inbox?.userId ?? this.ctx.store.getCursor("user-id");
    if (!chat || !user)
      throw new Error("Cannot route approval without an authorized chat");
    const language =
      this.ctx.store.getCursor("language") === "ru" ? "ru" : "en";
    if (event.kind === "question-requested" && hasSecretQuestion(event.data)) {
      await this.rejectSecret(event, turn, language);
      return;
    }
    await this.pending.request(event, turn.id, chat, user, language);
  }
  private async rejectSecret(
    event: AdapterEvent,
    turn: Turn,
    language: "ru" | "en",
  ): Promise<void> {
    if (event.turnId) {
      const key = `secret-interrupt/${turn.id}`;
      this.ctx.store.setCursor(key, event.turnId);
      try {
        await this.ctx.adapter.interrupt(event.turnId);
        this.ctx.store.setCursor(key, "");
      } catch {
        this.ctx.log("secret_interrupt_failed", { code: "unconfirmed" });
      }
    }
    if (this.delivery.finish(turn, "failed", catalog[language].failed))
      consumeTurnInbox(this.ctx, turn.id);
    await this.pending.cancelTurn(turn.id);
  }
  private async complete(event: AdapterEvent, turn: Turn): Promise<void> {
    if (!["submitting", "active", "uncertain"].includes(turn.status)) {
      await this.repairTerminal(turn);
      return;
    }
    if (await this.completeUserStop(turn)) return;
    const status = completionStatus(event);
    if (isCompactionOnly(this.ctx, turn)) {
      await this.completeCompaction(turn, status);
      return;
    }
    const message = completionText(this.ctx, turn, event, status);
    if (this.delivery.finish(turn, status, message)) {
      consumeTurnInbox(this.ctx, turn.id);
      await this.pending.cancelTurn(turn.id);
      this.completeFollowups(turn, status);
    }
  }
  private async completeCompaction(
    turn: Turn,
    status: "completed" | "failed" | "interrupted",
  ): Promise<void> {
    const language =
      this.ctx.store.getCursor("language") === "ru" ? "ru" : "en";
    const success = status === "completed";
    if (
      this.delivery.finish(
        turn,
        status,
        success ? "" : catalog[language].compactFailed,
        { silent: success },
      )
    ) {
      consumeTurnInbox(this.ctx, turn.id);
      await this.pending.cancelTurn(turn.id);
      this.completeFollowups(turn, status);
    }
  }
  private async completeUserStop(turn: Turn): Promise<boolean> {
    if (this.ctx.store.getCursor(`user-stop/${turn.id}`) !== "1") return false;
    if (this.delivery.finish(turn, "cancelled", "", { silent: true })) {
      consumeTurnInbox(this.ctx, turn.id);
      this.ctx.store.setCursor("stop-pending", "0");
      await this.pending.cancelTurn(turn.id);
      this.completeFollowups(turn, "cancelled");
    }
    return true;
  }
  async repairTerminal(turn: Turn): Promise<void> {
    if (
      !["completed", "failed", "cancelled", "interrupted"].includes(
        turn.status,
      ) ||
      turn.result === null
    )
      return;
    await this.pending.cancelTurn(turn.id);
    this.completeFollowups(
      turn,
      turn.status as "completed" | "failed" | "cancelled" | "interrupted",
    );
  }
  async repairFollowups(): Promise<void> {
    for (const turn of this.ctx.store.listTurns([
      "completed",
      "failed",
      "cancelled",
      "interrupted",
    ])) {
      if (
        turn.entityId === this.ctx.entityId &&
        turn.sessionId === this.ctx.sessionId &&
        turn.externalTurnId
      )
        await this.repairTerminal(turn);
    }
  }
  private completeFollowups(
    turn: Turn,
    status: "completed" | "failed" | "cancelled" | "interrupted",
  ): void {
    if (!turn.externalTurnId) return;
    for (const followup of this.ctx.store.listTurns([
      "active",
      "uncertain",
      "submitting",
    ])) {
      if (
        followup.id === turn.id ||
        followup.sessionId !== turn.sessionId ||
        followup.externalTurnId !== turn.externalTurnId
      )
        continue;
      if (this.delivery.finish(followup, status, "", { silent: true }))
        consumeTurnInbox(this.ctx, followup.id);
    }
  }
}
function hasSecretQuestion(data: unknown): boolean {
  if (
    !data ||
    typeof data !== "object" ||
    !("questions" in data) ||
    !Array.isArray(data.questions)
  )
    return false;
  return data.questions.some((question: unknown) =>
    Boolean(
      question &&
      typeof question === "object" &&
      "isSecret" in question &&
      question.isSecret === true,
    ),
  );
}
