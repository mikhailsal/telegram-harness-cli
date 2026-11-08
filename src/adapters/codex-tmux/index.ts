import { randomUUID } from "node:crypto";
import { realpath } from "node:fs/promises";
import { join } from "node:path";
import { UnsupportedCapabilityError } from "../contract.ts";
import type {
  AdapterAttachment,
  AdapterCapability,
  AdapterEvent,
  AdapterInput,
  AdapterSession,
  HarnessAdapter,
} from "../contract.ts";
import { matchingRollout, rolloutIdentity, rolloutPaths } from "./files.ts";
import { isCleanBusyPane, isExpectedPaste, parsePane } from "./pane.ts";
import { isAbort, readRollout } from "./rollout.ts";
import { validateDescriptor } from "./restore.ts";
import { TmuxRunner } from "./runner.ts";
import type { PaneIdentity } from "./runner.ts";
import { waitForEmptyComposer } from "./ui.ts";

export interface PendingSubmission {
  correlationId: string;
  envelope: string;
}
export interface TmuxDescriptor {
  server: string;
  localId: string;
  identity: PaneIdentity;
  cwd: string;
  threadId: string;
  rolloutPath: string;
  cursor: number;
  startedAt: number;
  originator: string;
}
export interface TmuxOptions {
  cwd: string;
  codexHome: string;
  executable: string;
  sandbox: "read-only" | "workspace-write" | "danger-full-access";
  persistSubmission: (submission: PendingSubmission) => Promise<void>;
  ackTimeoutMs?: number;
  pollMs?: number;
}
export class SubmitUncertainError extends Error {
  constructor() {
    super(
      "Codex input was sent but its rollout acknowledgement was not observed",
    );
    this.name = "SubmitUncertainError";
  }
}
export function messageEnvelope(text: string, timestamp: Date): string {
  return `[${timestamp.toISOString()}]\n<telegram-message>\n${text}\n</telegram-message>`;
}
function pastePayload(envelope: string): string {
  // Codex expands large pastes before trimming, keeping spaces inside the envelope intact.
  return envelope + " ".repeat(Math.max(0, 1001 - Array.from(envelope).length));
}
const delay = async (ms: number): Promise<void> =>
  new Promise((resolve) => setTimeout(resolve, ms));

export class CodexTmuxAdapter implements HarnessAdapter {
  readonly capabilities: ReadonlySet<AdapterCapability> = new Set([
    "submit",
    "observe",
    "interrupt",
    "reconcile",
    "newSession",
    "attach",
  ]);
  private serverName: string;
  private runner: TmuxRunner;
  get server(): string {
    return this.serverName;
  }
  private readonly sent = new Map<string, string>();
  private identity: PaneIdentity | undefined;
  private localId = "";
  private threadId = "";
  private rollout = "";
  private offset = 0;
  private running = false;
  private submitting = false;
  private cwd = "";
  private startedAt = 0;
  private originator = "";
  readonly options: TmuxOptions;
  constructor(options: TmuxOptions) {
    this.options = options;
    this.serverName = `thc-${randomUUID()}`;
    this.runner = new TmuxRunner(this.serverName);
  }

  async newSession(): Promise<AdapterSession> {
    if (this.identity) throw new Error("Adapter already owns a session");
    this.cwd = await realpath(this.options.cwd);
    this.localId = randomUUID();
    this.originator = `thc-${randomUUID()}`;
    this.startedAt = Date.now();
    this.identity = await this.runner.start(
      this.cwd,
      this.options.codexHome,
      this.options.executable,
      this.options.sandbox,
      this.originator,
    );
    this.running = true;
    return { id: this.localId, cursor: "0" };
  }
  async submit(input: AdapterInput): Promise<{ turnId?: string }> {
    if (this.submitting || this.sent.has(input.correlationId))
      throw new Error(
        "Input is already in progress or was previously submitted",
      );
    this.submitting = true;
    try {
      return await this.submitOnce(input);
    } finally {
      this.submitting = false;
    }
  }
  private async submitOnce(input: AdapterInput): Promise<{ turnId?: string }> {
    const identity = this.requireIdentity();
    if (
      this.rollout &&
      (await rolloutIdentity(this.rollout, this.cwd, this.originator)) !==
        this.threadId
    )
      throw new Error("Codex thread identity changed");
    await waitForEmptyComposer(
      this.runner,
      identity,
      this.cwd,
      this.options.ackTimeoutMs ?? 10000,
      this.options.pollMs ?? 250,
    );
    const text = messageEnvelope(input.text, new Date());
    if ([...this.sent.values()].includes(text))
      throw new Error("Envelope already belongs to another input");
    await this.options.persistSubmission({
      correlationId: input.correlationId,
      envelope: text,
    });
    this.sent.set(input.correlationId, text);
    const payload = pastePayload(text);
    await this.runner.paste(identity, payload);
    await delay(250);
    if (
      !isExpectedPaste(parsePane(await this.runner.capture(identity)), payload)
    )
      throw new Error("Composer does not match submitted input");
    try {
      await this.runner.key(identity, "Enter");
      if (await this.awaitAcknowledgement(text, payload, identity)) return {};
    } catch {
      throw new SubmitUncertainError();
    }
    throw new SubmitUncertainError();
  }
  private async awaitAcknowledgement(
    text: string,
    payload: string,
    identity: PaneIdentity,
  ): Promise<boolean> {
    const deadline = Date.now() + (this.options.ackTimeoutMs ?? 10000);
    let cursor = this.offset;
    const retryAt = Date.now() + 500;
    let retried = false;
    while (Date.now() < deadline) {
      let observed = await this.acknowledgement(text, cursor);
      if (observed.matched) return true;
      cursor = observed.cursor;
      if (!retried && Date.now() >= retryAt) {
        retried = true;
        await this.runner.guardWorkspace(identity, this.cwd);
        const pane = parsePane(await this.runner.capture(identity));
        observed = await this.acknowledgement(text, cursor);
        if (observed.matched) return true;
        cursor = observed.cursor;
        if (isExpectedPaste(pane, payload))
          await this.runner.key(identity, "Enter");
      }
      await delay(this.options.pollMs ?? 250);
    }
    return false;
  }
  private async acknowledgement(
    text: string,
    cursor: number,
  ): Promise<{ matched: boolean; cursor: number }> {
    await this.discoverRollout();
    if (!this.rollout) return { matched: false, cursor };
    const result = await readRollout(this.rollout, this.localId, cursor);
    return {
      matched: result.events.some(
        (event) => event.kind === "input" && event.text === text,
      ),
      cursor: result.offset,
    };
  }
  private async discoverRollout(): Promise<void> {
    if (this.rollout) return;
    const paths = await rolloutPaths(
      join(this.options.codexHome, "sessions"),
      this.startedAt,
    );
    const candidates = await Promise.all(
      paths.map(async (path) => ({
        path,
        id: await matchingRollout(
          path,
          this.cwd,
          this.originator,
          this.startedAt,
        ),
      })),
    );
    const matches = candidates.filter(
      (candidate) => candidate.id !== undefined,
    );
    if (matches.length > 1) throw new Error("Ambiguous rollout identity");
    const candidate = matches[0];
    if (!candidate?.id) return;
    this.threadId = candidate.id;
    this.rollout = candidate.path;
  }
  async *observe(): AsyncIterable<AdapterEvent> {
    while (this.running) {
      await this.discoverRollout();
      if (this.rollout) {
        const result = await readRollout(
          this.rollout,
          this.localId,
          this.offset,
        );
        for (const event of result.events) yield this.correlate(event);
        this.offset = result.offset;
      }
      await delay(this.options.pollMs ?? 250);
    }
  }
  async reconcile(cursor = "0"): Promise<AdapterEvent[]> {
    await this.discoverRollout();
    if (!this.rollout) return [];
    const offset = Number(cursor);
    const events: AdapterEvent[] = [];
    let current = offset;
    for (;;) {
      const result = await readRollout(this.rollout, this.localId, current);
      events.push(...result.events.map((event) => this.correlate(event)));
      if (result.offset === current) return events;
      current = result.offset;
    }
  }
  private correlate(event: AdapterEvent): AdapterEvent {
    if (event.kind !== "input") return event;
    for (const [correlationId, text] of this.sent)
      if (event.text === text) return { ...event, correlationId };
    return event;
  }
  async interrupt(turnId: string): Promise<void> {
    const identity = this.requireIdentity();
    await this.discoverRollout();
    if (
      !this.rollout ||
      (await rolloutIdentity(this.rollout, this.cwd, this.originator)) !==
        this.threadId
    )
      throw new Error("Codex rollout identity unavailable");
    const before = await this.activeTurn();
    if (before.turnId !== turnId)
      throw new Error("Requested turn is not active");
    if (!isCleanBusyPane(await this.runner.capture(identity)))
      throw new Error("Codex busy pane is ambiguous");
    const confirmed = await this.activeTurn();
    if (confirmed.turnId !== turnId)
      throw new Error("Requested turn is no longer active");
    await this.runner.key(identity, "Escape");
    await this.waitForAbort(turnId, confirmed.cursor);
  }
  private async activeTurn(): Promise<{
    turnId: string | undefined;
    cursor: number;
  }> {
    let cursor = 0;
    let turnId: string | undefined;
    for (;;) {
      const result = await readRollout(this.rollout, this.localId, cursor);
      for (const event of result.events) {
        if (event.kind === "turn-started") turnId = event.turnId;
        if (
          event.kind === "turn-completed" &&
          (!event.turnId || event.turnId === turnId)
        )
          turnId = undefined;
      }
      if (cursor === result.offset) return { turnId, cursor };
      cursor = result.offset;
    }
  }
  private async waitForAbort(turnId: string, start: number): Promise<void> {
    const deadline = Date.now() + (this.options.ackTimeoutMs ?? 10000);
    let cursor = start;
    while (Date.now() < deadline) {
      const result = await readRollout(this.rollout, this.localId, cursor);
      for (const event of result.events) {
        if (event.kind !== "turn-completed" || event.turnId !== turnId)
          continue;
        if (isAbort(event)) return;
        throw new Error("Turn completed without an interrupt acknowledgement");
      }
      cursor = result.offset;
      await delay(this.options.pollMs ?? 250);
    }
    throw new Error("Interrupt acknowledgement not observed");
  }
  approve(): Promise<void> {
    return Promise.reject(new UnsupportedCapabilityError("approve"));
  }
  async attach(): Promise<AdapterAttachment> {
    await this.runner.guard(this.requireIdentity());
    return {
      command: "tmux",
      args: ["-L", this.server, "attach-session", "-t", "codex"],
    };
  }
  close(): Promise<void> {
    this.running = false;
    return Promise.resolve();
  }
  async terminateSession(): Promise<void> {
    if (!this.identity) throw new Error("No owned Codex session");
    await this.runner.guard(this.identity);
    await this.runner.run("kill-server");
    this.identity = undefined;
    this.running = false;
  }
  snapshot(): TmuxDescriptor {
    return {
      server: this.server,
      localId: this.localId,
      identity: this.requireIdentity(),
      cwd: this.cwd,
      threadId: this.threadId,
      rolloutPath: this.rollout,
      cursor: this.offset,
      startedAt: this.startedAt,
      originator: this.originator,
    };
  }
  static async restore(
    options: TmuxOptions,
    descriptor: TmuxDescriptor,
    pending: PendingSubmission[],
  ): Promise<CodexTmuxAdapter> {
    if (!/^thc-[0-9a-f-]{36}$/u.test(descriptor.server))
      throw new Error("Invalid dedicated tmux server name");
    const adapter = new CodexTmuxAdapter(options);
    adapter.serverName = descriptor.server;
    adapter.runner = new TmuxRunner(descriptor.server);
    await validateDescriptor(options, descriptor, adapter.runner);
    adapter.identity = descriptor.identity;
    adapter.localId = descriptor.localId;
    adapter.cwd = descriptor.cwd;
    adapter.threadId = descriptor.threadId;
    adapter.rollout = descriptor.rolloutPath;
    adapter.offset = descriptor.cursor;
    adapter.startedAt = descriptor.startedAt;
    adapter.originator = descriptor.originator;
    for (const item of pending)
      adapter.sent.set(item.correlationId, item.envelope);
    adapter.running = true;
    return adapter;
  }
  getThreadIdentity(): { threadId: string; rolloutPath: string } | undefined {
    return this.threadId
      ? { threadId: this.threadId, rolloutPath: this.rollout }
      : undefined;
  }
  private requireIdentity(): PaneIdentity {
    if (!this.running || !this.identity)
      throw new Error("Codex tmux session is not running");
    return this.identity;
  }
}
