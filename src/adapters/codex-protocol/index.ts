import type {
  AdapterAttachment,
  AdapterCapability,
  AdapterEvent,
  AdapterInput,
  AdapterSession,
  ApprovalDecision,
  HarnessAdapter,
} from "../contract.ts";
import { EventQueue } from "./queue.ts";
import { ProtocolTransport, RpcError } from "./transport.ts";
import { SteerRejectedError } from "../contract.ts";
import { historyEvents, itemEvent } from "./events.ts";
import { itemProgress, ReasoningProgress } from "./progress.ts";
import type { Notification } from "./schema.ts";
import {
  commandResult,
  questionResponse,
  pendingStatus,
  notification,
  threadResult,
  turnList,
  turnResult,
} from "./schema.ts";

export interface ProtocolOptions {
  socketPath: string;
  cwd: string;
  sessionId?: string;
  sandbox?: string;
  approvalPolicy?: string;
  codexPath?: string;
  uncertainCorrelationIds?: readonly string[];
  uncertainApprovalIds?: readonly string[];
}
export interface CommandContext {
  cwd: string;
  env: Record<string, string | null>;
  sandboxPolicy: Record<string, unknown>;
}
export class ProtocolAdapter implements HarnessAdapter {
  readonly capabilities: ReadonlySet<AdapterCapability> = new Set([
    "submit",
    "observe",
    "interrupt",
    "approve",
    "reconcile",
    "newSession",
    "attach",
    "steer",
    "compact",
  ]);
  private readonly options: ProtocolOptions;
  private readonly queue = new EventQueue<AdapterEvent>();
  private readonly transport: ProtocolTransport;
  private sessionId: string | undefined;
  private connected = false;
  private ready = false;
  private readonly submitted = new Set<string>();
  private readonly emitted = new Set<string>();
  private readonly reasoning = new ReasoningProgress();
  private readonly approvals = new Map<
    string,
    { id: string | number; question: boolean }
  >();
  private readonly sentApprovals = new Set<string>();
  private readonly approvalWaiters = new Map<
    string,
    { resolve: () => void; reject: (error: Error) => void }
  >();
  constructor(options: ProtocolOptions) {
    this.options = options;
    this.sessionId = options.sessionId;
    for (const id of options.uncertainCorrelationIds ?? [])
      this.submitted.add(id);
    for (const id of options.uncertainApprovalIds ?? [])
      this.sentApprovals.add(id);
    this.transport = new ProtocolTransport(
      (message) => {
        this.handle(message);
      },
      () => {
        this.disconnected();
      },
    );
  }
  private async connect(): Promise<void> {
    if (this.connected) return;
    await this.transport.connect(this.options.socketPath);
    try {
      if (this.sessionId)
        await this.transport.call("thread/resume", {
          threadId: this.sessionId,
        });
      this.connected = true;
    } catch (error) {
      this.transport.close();
      throw error;
    }
  }
  async newSession(): Promise<AdapterSession> {
    await this.connect();
    const result = threadResult.parse(
      await this.transport.call("thread/start", {
        cwd: this.options.cwd,
        sandbox: this.options.sandbox ?? "workspace-write",
        approvalPolicy: this.options.approvalPolicy ?? "on-request",
      }),
    );
    this.sessionId = result.thread.id;
    this.submitted.clear();
    this.ready = true;
    return { id: result.thread.id };
  }
  async submit(input: AdapterInput): Promise<{ turnId?: string }> {
    if (!this.ready || !this.sessionId)
      throw new Error("Reconcile the active session before submitting");
    if (this.submitted.has(input.correlationId))
      throw new Error("Correlation ID already submitted or observed");
    this.submitted.add(input.correlationId);
    const result = await this.transport.call("turn/start", {
      threadId: this.sessionId,
      clientUserMessageId: input.correlationId,
      input: [{ type: "text", text: input.text, text_elements: [] }],
    });
    return { turnId: turnResult.parse(result).turn.id };
  }
  async steer(input: AdapterInput, turnId: string): Promise<void> {
    if (!this.ready || !this.sessionId)
      throw new Error("Reconcile the active session before steering");
    if (this.submitted.has(input.correlationId))
      throw new Error("Correlation ID already submitted or observed");
    this.submitted.add(input.correlationId);
    try {
      await this.transport.call("turn/steer", {
        threadId: this.sessionId,
        expectedTurnId: turnId,
        clientUserMessageId: input.correlationId,
        input: [{ type: "text", text: input.text, text_elements: [] }],
      });
    } catch (error) {
      if (
        error instanceof RpcError &&
        error.code === -32600 &&
        error.message === "no active turn to steer"
      ) {
        this.submitted.delete(input.correlationId);
        throw new SteerRejectedError();
      }
      throw error;
    }
  }
  async compact(): Promise<void> {
    if (!this.ready || !this.sessionId) throw new Error("Session is not ready");
    await this.transport.call("thread/compact/start", {
      threadId: this.sessionId,
    });
  }
  observe(): AsyncIterable<AdapterEvent> {
    return this.queue;
  }
  async interrupt(turnId: string): Promise<void> {
    if (!this.ready || !this.sessionId) throw new Error("Session is not ready");
    await this.transport.call("turn/interrupt", {
      threadId: this.sessionId,
      turnId,
    });
  }
  async approve(decision: ApprovalDecision): Promise<void> {
    if (!this.ready) throw new Error("Session is not ready");
    const request = this.approvals.get(decision.requestId);
    if (request === undefined || this.sentApprovals.has(decision.requestId))
      throw new Error("Approval is not pending");
    const result = request.question
      ? questionResponse.parse(decision.decision)
      : { decision: decision.decision };
    this.sentApprovals.add(decision.requestId);
    const resolved = new Promise<void>((resolve, reject) =>
      this.approvalWaiters.set(decision.requestId, { resolve, reject }),
    );
    try {
      this.transport.send({ id: request.id, result });
    } catch (error) {
      this.approvalWaiters.delete(decision.requestId);
      throw error;
    }
    return resolved;
  }
  async reconcile(cursor?: string): Promise<AdapterEvent[]> {
    this.ready = false;
    await this.connect();
    if (!this.sessionId) throw new Error("No session to reconcile");
    const events = await this.readHistory(this.sessionId);
    for (const event of events)
      if (event.correlationId) this.submitted.add(event.correlationId);
    const index =
      cursor === undefined
        ? -1
        : events.findIndex((event) => event.cursor === cursor);
    if (cursor !== undefined && index < 0)
      throw new Error("History cursor not found; full reconciliation required");
    this.ready = true;
    return events.slice(index + 1);
  }
  private async readHistory(sessionId: string): Promise<AdapterEvent[]> {
    let cursor: string | undefined;
    const turns = [];
    do {
      const page = turnList.parse(
        await this.transport.call("thread/turns/list", {
          threadId: sessionId,
          limit: 100,
          itemsView: "full",
          sortDirection: "asc",
          ...(cursor ? { cursor } : {}),
        }),
      );
      turns.push(...page.data);
      cursor = page.nextCursor ?? undefined;
    } while (cursor);
    return turns.flatMap((turn) => historyEvents(sessionId, turn));
  }
  async pendingState(): Promise<"none" | "some" | "unknown"> {
    if (!this.connected || !this.sessionId) return "unknown";
    const parsed = pendingStatus.safeParse(
      await this.transport.call("thread/read", {
        threadId: this.sessionId,
        includeTurns: false,
      }),
    );
    if (!parsed.success) return "unknown";
    const status = parsed.data.thread.status;
    if (status.type === "idle") return "none";
    if (status.type !== "active") return "unknown";
    return status.activeFlags.length > 0 ? "some" : "none";
  }
  async commandExec(
    command: string[],
    context: CommandContext,
  ): Promise<{ exitCode: number; stdout: string; stderr: string }> {
    if (!this.ready || !this.sessionId) throw new Error("Session is not ready");
    if (!command.length || !context.cwd || !context.sandboxPolicy.type)
      throw new Error(
        "Command, cwd, environment and sandbox policy are required",
      );
    return commandResult.parse(
      await this.transport.call("command/exec", { command, ...context }),
    );
  }
  attach(): Promise<AdapterAttachment> {
    if (!this.sessionId) throw new Error("No active session");
    return Promise.resolve({
      command: this.options.codexPath ?? "codex",
      args: [
        "--remote",
        `unix://${this.options.socketPath}`,
        "resume",
        this.sessionId,
      ],
    });
  }
  close(): Promise<void> {
    this.ready = false;
    this.transport.close();
    this.queue.close();
    return Promise.resolve();
  }
  private handle(raw: unknown): void {
    const parsed = notification.safeParse(raw);
    if (
      !parsed.success ||
      !this.sessionId ||
      parsed.data.params.threadId !== this.sessionId
    )
      return;
    const message = parsed.data;
    if (
      (message.method.endsWith("/requestApproval") ||
        message.method === "item/tool/requestUserInput") &&
      message.id !== undefined
    ) {
      this.approvalRequested(message, message.id);
      return;
    }
    if (
      message.method === "serverRequest/resolved" &&
      message.params.requestId !== undefined
    ) {
      this.approvalResolved(message.params.requestId);
      return;
    }
    this.handleTurn(message);
  }
  private approvalRequested(message: Notification, id: string | number): void {
    const requestId = String(id);
    const question = message.method === "item/tool/requestUserInput";
    this.approvals.set(requestId, { id, question });
    if (!this.sessionId) return;
    this.queue.push({
      kind: question ? "question-requested" : "approval-requested",
      sessionId: this.sessionId,
      cursor: `approval/${requestId}`,
      ...(message.params.turnId ? { turnId: message.params.turnId } : {}),
      requestId,
      data: message.params,
    });
  }
  private approvalResolved(id: string | number): void {
    const requestId = String(id);
    this.approvals.delete(requestId);
    this.sentApprovals.delete(requestId);
    this.approvalWaiters.get(requestId)?.resolve();
    this.approvalWaiters.delete(requestId);
    if (!this.sessionId) return;
    this.queue.push({
      kind: "approval-resolved",
      sessionId: this.sessionId,
      cursor: `approval/${requestId}/resolved`,
      requestId,
    });
  }
  private handleTurn(message: Notification): void {
    if (!this.sessionId) return;
    if (
      message.method === "turn/started" ||
      message.method === "turn/completed"
    ) {
      this.handleTurnStatus(message);
    } else if (message.method === "item/started") {
      this.handleItemStarted(message);
    } else if (message.method === "item/reasoning/summaryTextDelta") {
      const progress = this.reasoning.update(this.sessionId, message);
      if (progress) this.queue.push(progress);
    } else if (message.method === "item/completed") {
      this.handleItem(message);
    }
  }
  private handleItemStarted(message: Notification): void {
    const { item, turnId } = message.params;
    if (!this.sessionId || !item || !turnId) return;
    const progress = itemProgress(this.sessionId, turnId, item);
    if (progress) this.queue.push(progress);
  }
  private handleTurnStatus(message: Notification): void {
    if (!this.sessionId || !message.params.turn) return;
    for (const event of historyEvents(this.sessionId, message.params.turn)) {
      if (message.method === "turn/completed" || event.kind === "turn-started")
        this.emit(event);
    }
  }
  private handleItem(message: Notification): void {
    const { item, turnId } = message.params;
    if (!this.sessionId || !item || !turnId) return;
    const event = itemEvent(this.sessionId, turnId, item);
    if (!event) return;
    if (event.correlationId) this.submitted.add(event.correlationId);
    this.emit(event);
  }
  private emit(event: AdapterEvent): void {
    if (this.emitted.has(event.cursor)) return;
    this.emitted.add(event.cursor);
    this.queue.push(event);
  }
  private disconnected(): void {
    this.connected = false;
    this.ready = false;
    this.approvals.clear();
    if (this.sessionId)
      this.queue.push({
        kind: "error",
        sessionId: this.sessionId,
        cursor: "connection/lost",
        text: "Protocol disconnected; reconcile before submitting",
      });
    for (const waiter of this.approvalWaiters.values())
      waiter.reject(
        new Error(
          "Disconnected before approval acknowledgement; reconcile before retry",
        ),
      );
    this.approvalWaiters.clear();
  }
}
