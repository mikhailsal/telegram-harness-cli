import { randomUUID } from "node:crypto";
import type { HarnessAdapter } from "../adapters/contract.js";
import { catalog } from "../channels/index.js";
import { RpcError } from "../adapters/codex-protocol/transport.js";
import { CodexTmuxAdapter } from "../adapters/codex-tmux/index.js";
import type { RuntimeContext } from "./context.js";
import type { TurnEngine } from "./engine.js";
import { describeAdapter, managedDescriptorSchema } from "./adapter.js";
import { sendWelcome } from "./runtime-poll.js";
import { delay, stopped, untilAbort } from "./runtime-util.js";

export type AdapterFactory = (
  context: RuntimeContext,
) => Promise<HarnessAdapter>;
export interface SupervisorOptions {
  factory: AdapterFactory;
  freshFactory: AdapterFactory;
  startDaemon: () => Promise<void>;
}
function daemonUnavailable(error: unknown): boolean {
  return (
    error instanceof Error &&
    /socket|connect|offline|daemon|websocket|ECONNREFUSED|ENOENT/i.test(
      error.message,
    )
  );
}
export class Supervisor {
  private readonly ctx: RuntimeContext;
  private readonly engine: TurnEngine;
  private readonly options: SupervisorOptions;
  constructor(
    ctx: RuntimeContext,
    engine: TurnEngine,
    options: SupervisorOptions,
  ) {
    this.ctx = ctx;
    this.engine = engine;
    this.options = options;
  }
  private async activate(
    adapter: HarnessAdapter,
    previousSessionId?: string,
  ): Promise<void> {
    const session = await adapter.newSession();
    if (stopped(this.ctx.signal))
      throw new Error("Runtime stopped while creating a session");
    const now = this.ctx.now().toISOString();
    const id = randomUUID();
    const row = {
      id,
      entityId: this.ctx.entityId,
      harness: this.ctx.config.codex.adapter,
      externalId: session.id,
      startedAt: now,
      endedAt: null,
    };
    if (
      !this.ctx.store.activateSession(
        row,
        describeAdapter(adapter, session.id),
        previousSessionId,
        session.cursor ?? "",
      )
    )
      throw new Error("Session activation lost its expected state");
    this.ctx.sessionId = id;
  }
  private async connect(): Promise<HarnessAdapter> {
    let adapter = await this.options.factory(this.ctx);
    if (stopped(this.ctx.signal)) {
      await adapter.close();
      throw new Error("Runtime stopped");
    }
    this.ctx.adapter = adapter;
    const descriptor = this.ctx.store.getCursor("adapter-descriptor");
    const restored = descriptor
      ? await this.restoreOrReplace(adapter, descriptor)
      : { adapter, replay: await this.startFresh(adapter) };
    adapter = restored.adapter;
    const replay = restored.replay;
    if (stopped(this.ctx.signal)) throw new Error("Runtime stopped");
    await sendWelcome(this.ctx);
    if (stopped(this.ctx.signal)) throw new Error("Runtime stopped");
    this.ctx.adapterReady = true;
    await this.engine.recover(replay);
    if (stopped(this.ctx.signal)) throw new Error("Runtime stopped");
    if (this.ctx.store.getCursor("poll-conflict") !== "1")
      await this.ctx.notice.healthy().catch(() => undefined);
    this.ctx.store.setCursor("runtime-ready", this.ctx.now().toISOString());
    return adapter;
  }
  private async startFresh(
    adapter: HarnessAdapter,
  ): Promise<import("../adapters/contract.js").AdapterEvent[]> {
    if (
      this.ctx.store.getCursor("active-session") ||
      this.ctx.store.listTurns(["submitting", "active", "uncertain"]).length
    )
      throw new Error("Unresolved state requires the saved adapter descriptor");
    await this.activate(adapter);
    return [];
  }
  private async restore(
    adapter: HarnessAdapter,
    descriptor: string,
  ): Promise<import("../adapters/contract.js").AdapterEvent[]> {
    const id = this.ctx.store.getCursor("active-session");
    const session = id ? this.ctx.store.getSession(id) : undefined;
    if (session?.endedAt !== null)
      throw new Error("Saved adapter session is missing or closed");
    const saved = managedDescriptorSchema.parse(
      JSON.parse(descriptor) as unknown,
    );
    const externalId =
      saved.kind === "protocol" ? saved.sessionId : saved.descriptor.localId;
    if (session.externalId !== externalId)
      throw new Error("Saved adapter session identity does not match");
    this.ctx.sessionId = session.id;
    const cursor = this.ctx.store.getCursor("adapter-cursor");
    return adapter.reconcile(cursor === "" ? undefined : cursor);
  }
  private missingRollout(error: unknown): boolean {
    const session = this.ctx.store.getSession(this.ctx.sessionId);
    return (
      error instanceof RpcError &&
      error.code === -32600 &&
      this.ctx.config.codex.adapter === "protocol" &&
      session !== undefined &&
      error.message === `no rollout found for thread id ${session.externalId}`
    );
  }
  private canReplaceIdle(): boolean {
    return (
      this.ctx.store.listTurns(["submitting", "active", "uncertain"]).length ===
        0 && this.ctx.store.listRequests(["pending", "answering"]).length === 0
    );
  }
  private hasMaterializedHistory(): boolean {
    return this.ctx.store
      .listTurns(["completed"])
      .some(
        (turn) =>
          turn.sessionId === this.ctx.sessionId && turn.externalTurnId !== null,
      );
  }
  private canInterruptLostTurns(): boolean {
    const now = this.ctx.now().getTime();
    return this.ctx.store
      .listTurns(["submitting", "active", "uncertain"])
      .every((turn) => {
        if (turn.sessionId !== this.ctx.sessionId) return false;
        const updated = Date.parse(turn.updatedAt);
        return !(
          (turn.status === "submitting" || turn.status === "uncertain") &&
          (!Number.isFinite(updated) || now - updated < 30000)
        );
      });
  }
  private async interruptLostTurns(): Promise<void> {
    const session = this.ctx.store.getSession(this.ctx.sessionId);
    if (!session) throw new Error("Saved adapter session is missing");
    for (const turn of this.ctx.store.listTurns([
      "submitting",
      "active",
      "uncertain",
    ])) {
      if (turn.sessionId !== session.id) continue;
      await this.engine.event({
        kind: "turn-completed",
        sessionId: session.externalId,
        cursor: `lost/${turn.id}`,
        correlationId: turn.id,
        data: { status: "interrupted" },
      });
    }
  }
  private async restoreOrReplace(
    adapter: HarnessAdapter,
    descriptor: string,
  ): Promise<{
    adapter: HarnessAdapter;
    replay: import("../adapters/contract.js").AdapterEvent[];
  }> {
    try {
      return { adapter, replay: await this.restore(adapter, descriptor) };
    } catch (error) {
      if (!this.missingRollout(error)) throw error;
      if (!this.canReplaceIdle()) {
        if (!this.hasMaterializedHistory() || !this.canInterruptLostTurns())
          throw error;
        await this.interruptLostTurns();
        if (!this.canReplaceIdle()) throw error;
      }
      const replacement = await this.options.freshFactory(this.ctx);
      try {
        await this.activate(replacement, this.ctx.sessionId);
      } catch (failure) {
        await replacement.close();
        throw failure;
      }
      this.ctx.adapter = replacement;
      await adapter.close().catch(() => undefined);
      return { adapter: replacement, replay: [] };
    }
  }
  private async observe(adapter: HarnessAdapter): Promise<void> {
    const iterator = adapter.observe()[Symbol.asyncIterator]();
    try {
      while (!stopped(this.ctx.signal) && this.ctx.adapter === adapter) {
        const item = await untilAbort(iterator.next(), this.ctx.signal);
        if (!item || item.done || this.ctx.adapter !== adapter) return;
        if (item.value.kind === "error") {
          this.ctx.adapterReady = false;
          await this.engine.event(item.value);
          throw new Error("Adapter connection lost");
        }
        await this.engine.event(item.value);
      }
    } finally {
      if (stopped(this.ctx.signal) && iterator.return)
        void iterator.return().catch(() => undefined);
    }
  }
  private async observeActive(
    adapter: HarnessAdapter,
  ): Promise<HarnessAdapter | undefined> {
    await this.observe(adapter);
    if (this.ctx.adapter !== adapter) return this.ctx.adapter;
    if (!stopped(this.ctx.signal)) throw new Error("Adapter connection ended");
  }
  async run(): Promise<void> {
    let wait = 1000;
    let nextAdapter: HarnessAdapter | undefined;
    while (!stopped(this.ctx.signal)) {
      let adapter: HarnessAdapter | undefined;
      try {
        adapter =
          nextAdapter ?? (await untilAbort(this.connect(), this.ctx.signal));
        nextAdapter = undefined;
        if (!adapter) return;
        wait = 1000;
        nextAdapter = await this.observeActive(adapter);
        if (nextAdapter) continue;
      } catch (error) {
        if (await this.disconnected(adapter, error)) {
          nextAdapter = this.ctx.adapter;
          continue;
        }
      } finally {
        await this.closeFailed(adapter);
      }
      if (!stopped(this.ctx.signal)) await delay(wait, this.ctx.signal);
      wait = Math.min(wait * 2, 60000);
    }
  }
  private async closeFailed(
    adapter: HarnessAdapter | undefined,
  ): Promise<void> {
    if (adapter && this.ctx.adapter === adapter && !this.ctx.adapterReady)
      await adapter.close().catch(() => undefined);
  }
  private async disconnected(
    adapter: HarnessAdapter | undefined,
    error: unknown,
  ): Promise<boolean> {
    if (adapter && this.ctx.adapter !== adapter) return true;
    this.ctx.adapterReady = false;
    this.ctx.log("adapter.disconnected");
    await this.notifyAuth(error);
    if (!adapter) await this.ctx.adapter.close().catch(() => undefined);
    if (
      !stopped(this.ctx.signal) &&
      this.ctx.config.codex.adapter === "protocol" &&
      this.ctx.config.codex.daemon_autostart &&
      daemonUnavailable(error)
    )
      await untilAbort(
        this.options.startDaemon().catch(() => undefined),
        this.ctx.signal,
      );
    return false;
  }
  private async notifyAuth(error: unknown): Promise<void> {
    if (!(error instanceof Error) || stopped(this.ctx.signal)) return;
    const auth = /login|auth/i.test(error.message);
    const unavailable =
      this.ctx.config.codex.adapter === "protocol" &&
      !this.ctx.config.codex.daemon_autostart &&
      daemonUnavailable(error);
    if (!auth && !unavailable) return;
    const language =
      this.ctx.store.getCursor("language") === "ru" ? "ru" : "en";
    const chat =
      this.ctx.store.getCursor("chat-id") ??
      String(this.ctx.config.allowlist[0]);
    await this.ctx.notice
      .show(
        "startup/startup",
        chat,
        auth ? catalog[language].loginNeeded : catalog[language].down,
      )
      .catch(() => undefined);
  }
  async rotate(): Promise<void> {
    const prior = this.ctx.adapter;
    if (
      this.ctx.store.listTurns(["submitting", "active", "uncertain"]).length ||
      this.ctx.store.listRequests(["pending", "answering"]).length
    )
      throw new Error("Cannot rotate an active session");
    const next = await this.options.freshFactory(this.ctx);
    try {
      await this.activate(next, this.ctx.sessionId);
    } catch (error) {
      if (next instanceof CodexTmuxAdapter)
        await next.terminateSession().catch(() => undefined);
      await next.close();
      throw error;
    }
    this.ctx.adapter = next;
    this.ctx.adapterReady = true;
    if (prior instanceof CodexTmuxAdapter)
      await prior.terminateSession().catch(() => {
        this.ctx.log("adapter.previous-session-cleanup-failed");
      });
    await prior.close().catch(() => undefined);
  }
}
