import { ChannelStatus, catalog } from "../channels/index.js";
import type { AdapterEvent } from "../adapters/contract.js";
import type { Turn } from "../store/index.js";
import type { RuntimeContext } from "./context.js";
import { activeTurn } from "./events.js";
import { nextQueuedTurn } from "./queue.js";

interface Activity {
  kind: "thinking" | "command" | "read" | "file-change";
  detail?: string;
}
function activity(value: unknown): Activity | undefined {
  if (!value || typeof value !== "object" || !("kind" in value)) return;
  const item = value as { kind?: unknown; detail?: unknown };
  if (
    !["thinking", "command", "read", "file-change"].includes(String(item.kind))
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
  acknowledge(): void {
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
    const chat = this.ctx.store.getCursor("chat-id");
    if (!chat || this.ctx.config.status === "off") return;
    const status = new ChannelStatus(
      this.ctx.channel,
      Number(chat),
      Math.abs(
        turn.createdAt.length + Number.parseInt(turn.id.slice(0, 6), 16),
      ),
      this.ctx.transient,
      `status/${turn.id}`,
      this.ctx.config.status,
      this.ctx.now,
    );
    this.rows.set(turn.id, status);
    return status;
  }
  async pause(id: string): Promise<void> {
    await this.rows.get(id)?.pause();
  }
  resumeUnsent(id: string): void {
    const delivered = this.ctx.store
      .listOutbox(["sent"])
      .some((row) => row.turnId === id && row.chunk >= 0);
    if (!delivered) this.rows.get(id)?.resume();
  }
  async remove(id: string): Promise<void> {
    await this.rows.get(id)?.stop();
    this.rows.delete(id);
  }
  async cancelled(): Promise<void> {
    for (const id of this.rows.keys())
      if (
        this.ctx.store.getTurn(id)?.status === "cancelled" ||
        this.ctx.store.getCursor(`user-stop/${id}`) === "1"
      )
        await this.remove(id);
  }
  async close(): Promise<void> {
    for (const id of this.rows.keys()) await this.remove(id);
  }
}
