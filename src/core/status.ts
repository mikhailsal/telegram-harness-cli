import { ChannelStatus, catalog } from "../channels/index.js";
import type { AdapterEvent } from "../adapters/contract.js";
import type { Turn } from "../store/index.js";
import type { RuntimeContext } from "./context.js";
import { activeTurn } from "./events.js";
import { nextQueuedTurn } from "./queue.js";
import { turnDraftId } from "./turn-draft.js";
import { replySourceMessageId } from "./reply-target.js";
import { isCompactionOnly } from "./compaction.js";
import { turnChatId } from "./format.js";

interface Activity {
  kind: "thinking" | "command" | "read" | "file-change" | "compacting";
  detail?: string;
}
function activity(value: unknown): Activity | undefined {
  if (!value || typeof value !== "object" || !("kind" in value)) return;
  const item = value as { kind?: unknown; detail?: unknown };
  if (
    !["thinking", "command", "read", "file-change", "compacting"].includes(
      String(item.kind),
    )
  )
    return;
  return {
    kind: item.kind as Activity["kind"],
    ...(typeof item.detail === "string" ? { detail: item.detail } : {}),
  };
}
function short(value: string, limit: number): string {
  return Array.from(value.replace(/\s+/gu, " ").trim())
    .slice(0, limit)
    .join("");
}
function elapsed(start: string, now: number): string {
  const seconds = Math.max(0, Math.floor((now - Date.parse(start)) / 1000));
  return `${String(Math.floor(seconds / 60))}:${String(seconds % 60).padStart(2, "0")}`;
}
function activityLabel(
  value: Activity | undefined,
  strings: typeof catalog.en,
): string {
  if (!value || value.kind === "thinking")
    return `${strings.thinking}${value?.detail ? `: ${short(value.detail, 60)}` : ""}`;
  if (value.kind === "file-change")
    return `${strings.editing}${value.detail ? ` ${short(value.detail, 8)}` : ""}`;
  return `${value.kind === "read" ? strings.reading : strings.running} ${short(value.detail ?? "", 40)}`.trim();
}
export class TurnStatuses {
  private readonly ctx: RuntimeContext;
  private readonly rows = new Map<string, ChannelStatus>();
  private turnId = "";
  private lastEvent = 0;
  private current: Activity | undefined;
  private acknowledgedUntil = 0;
  constructor(ctx: RuntimeContext) {
    this.ctx = ctx;
  }
  event(event: AdapterEvent): void {
    const turn = activeTurn(this.ctx, event.turnId) ?? activeTurn(this.ctx);
    if (!turn) return;
    this.select(turn);
    this.lastEvent = this.ctx.now().getTime();
    if (event.kind === "progress") this.current = activity(event.data);
    else if (event.kind === "turn-started") this.current = { kind: "thinking" };
    this.refresh();
  }
  async acknowledge(): Promise<void> {
    const turn = activeTurn(this.ctx);
    if (turn) {
      this.requestMove(turn);
      await this.relocate(turn.id);
    }
    this.acknowledgedUntil = this.ctx.now().getTime() + 3000;
    this.refresh();
  }
  private select(turn: Turn): void {
    if (this.turnId === turn.id) return;
    this.turnId = turn.id;
    this.current = undefined;
    this.acknowledgedUntil = 0;
    this.lastEvent = this.ctx.now().getTime();
  }
  private label(turn: Turn, now: number): string {
    const strings =
      catalog[this.ctx.store.getCursor("language") === "ru" ? "ru" : "en"];
    if (!this.ctx.adapterReady) return strings.reconnecting;
    if (
      this.ctx.store
        .listRequests(["pending", "answering"])
        .some((row) => row.turnId === turn.id)
    )
      return strings.waiting;
    if (now < this.acknowledgedUntil) return strings.acknowledged;
    if (this.ctx.store.getCursor("deferred-new") === "1")
      return strings.newDeferred;
    if (turn.status === "submitting" || turn.status === "uncertain")
      return strings.submitting;
    if (this.current?.kind === "compacting") return strings.compacting;
    if (turn.source === "terminal") return strings.terminalWorking;
    return activityLabel(this.current, strings);
  }
  refresh(): void {
    if (this.ctx.config.status === "off") return;
    const turn =
      activeTurn(this.ctx) ??
      (!this.ctx.adapterReady ? nextQueuedTurn(this.ctx) : undefined);
    if (!turn || this.ctx.store.getCursor(`user-stop/${turn.id}`) === "1")
      return;
    this.select(turn);
    const now = this.ctx.now().getTime();
    this.status(turn)?.update(this.text(turn, now));
  }
  private text(turn: Turn, now: number): string {
    const strings =
      catalog[this.ctx.store.getCursor("language") === "ru" ? "ru" : "en"];
    const stalled =
      this.ctx.adapterReady &&
      now - this.lastEvent >= this.ctx.config.stall_timeout * 1000;
    const count = this.ctx.store
      .listTurns(["queued"])
      .filter(
        (row) => row.entityId === this.ctx.entityId && row.id !== turn.id,
      ).length;
    const delay = elapsed(new Date(this.lastEvent).toISOString(), now);
    return `${this.label(turn, now)} ${elapsed(turn.status === "queued" ? turn.createdAt : turn.updatedAt, now)}${stalled ? ` · ${strings.stalled} ${delay}` : ""}${count ? `\n${strings.queue}: ${String(count)}` : ""}`;
  }
  private status(turn: Turn): ChannelStatus | undefined {
    const old = this.rows.get(turn.id);
    if (old) return old;
    const chat = turnChatId(this.ctx, turn);
    if (this.ctx.config.status === "off") return;
    if (this.ctx.config.status === "draft")
      this.ctx.store.setCursor(`status-draft/${turn.id}`, "1");
    const key = `status-anchor/${turn.id}`;
    if (!this.ctx.store.getCursor(key))
      this.ctx.store.setCursor(
        key,
        String(replySourceMessageId(this.ctx.store, turn, chat) ?? ""),
      );
    const status = new ChannelStatus(
      this.ctx.channel,
      Number(chat),
      turnDraftId(turn),
      this.ctx.transient,
      `status/${turn.id}`,
      this.ctx.config.status,
      this.ctx.now,
    );
    this.rows.set(turn.id, status);
    return status;
  }
  async pause(id: string): Promise<boolean> {
    if (!(await this.relocate(id))) return false;
    await this.rows.get(id)?.pause();
    return true;
  }
  private requestMove(turn: Turn, force = false): void {
    const chat = turnChatId(this.ctx, turn);
    const target = replySourceMessageId(this.ctx.store, turn, chat);
    if (
      force ||
      (target !== undefined &&
        String(target) !== this.ctx.store.getCursor(`status-anchor/${turn.id}`))
    )
      this.ctx.store.setCursor(`status-move/${turn.id}`, String(target ?? 0));
  }
  private pendingMoves(id?: string): Turn[] {
    return this.ctx.store
      .listTurns([
        "active",
        "submitting",
        "uncertain",
        "completed",
        "failed",
        "cancelled",
        "interrupted",
      ])
      .filter(
        (turn) =>
          turn.entityId === this.ctx.entityId &&
          (id === undefined || id === turn.id) &&
          Boolean(this.ctx.store.getCursor(`status-move/${turn.id}`)),
      );
  }
  hasRelocations(): boolean {
    return this.pendingMoves().length > 0;
  }
  async relocate(id?: string): Promise<boolean> {
    for (const turn of this.pendingMoves(id))
      if (!(await this.relocateTurn(turn))) return false;
    return true;
  }
  private async relocateTurn(turn: Turn): Promise<boolean> {
    const key = `status-move/${turn.id}`;
    const target = this.ctx.store.getCursor(key);
    if (!target) return true;
    const recovered =
      !this.rows.has(turn.id) &&
      this.ctx.store.getCursor(`status-draft/${turn.id}`) === "1";
    const status = this.status(turn);
    if (status) {
      status.update(this.text(turn, this.ctx.now().getTime()));
      if (!(await status.detach(recovered))) return false;
    }
    this.ctx.store.setCursor(`status-anchor/${turn.id}`, target);
    this.ctx.store.setCursor(key, "");
    if (["active", "submitting", "uncertain"].includes(turn.status))
      status?.resume();
    return true;
  }
  resumeUnsent(id: string): void {
    const delivered = this.ctx.store
      .listOutbox(["sent", "pending", "sending", "failed"])
      .some(
        (row) => row.turnId === id && row.chunk >= 0 && row.messageId !== null,
      );
    if (!delivered) this.rows.get(id)?.resume();
  }
  async remove(
    id: string,
    draft: "preserve" | "discard" | "recover" = "discard",
  ): Promise<boolean> {
    if ((await this.rows.get(id)?.stop(draft)) === false) return false;
    this.rows.delete(id);
    if (draft !== "preserve") {
      this.ctx.store.setCursor(`status-draft/${id}`, "");
      this.ctx.store.setCursor(`status-move/${id}`, "");
    }
    return true;
  }
  private recoverCancelled(): Set<string> {
    const recovered = new Set<string>();
    for (const turn of this.ctx.store.listTurns([
      "submitting",
      "active",
      "uncertain",
      "cancelled",
      "completed",
    ])) {
      if (
        turn.entityId !== this.ctx.entityId ||
        this.rows.has(turn.id) ||
        !discardStatus(this.ctx, turn) ||
        this.ctx.store.getCursor(`status-draft/${turn.id}`) !== "1"
      )
        continue;
      this.status(turn)?.update(this.text(turn, this.ctx.now().getTime()));
      recovered.add(turn.id);
    }
    return recovered;
  }
  async cancelled(): Promise<boolean> {
    const recovered = this.recoverCancelled();
    let cleared = true;
    for (const id of this.rows.keys()) {
      const turn = this.ctx.store.getTurn(id);
      if (turn && discardStatus(this.ctx, turn))
        if (!(await this.remove(id, recovered.has(id) ? "recover" : "discard")))
          cleared = false;
    }
    return cleared;
  }
  async close(): Promise<void> {
    for (const id of this.rows.keys()) await this.remove(id, "preserve");
  }
}
function discardStatus(ctx: RuntimeContext, turn: Turn): boolean {
  return (
    turn.status === "cancelled" ||
    ctx.store.getCursor(`user-stop/${turn.id}`) === "1" ||
    (turn.status === "completed" && isCompactionOnly(ctx, turn))
  );
}
