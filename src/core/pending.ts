import { randomUUID } from "node:crypto";
import type { AdapterEvent } from "../adapters/contract.ts";
import { catalog, sendApproval, transientIntent } from "../channels/index.ts";
import type { ChannelEvent, Language } from "../channels/types.ts";
import type { PendingRequest } from "../store/index.ts";
import type { RuntimeContext } from "./context.ts";
import {
  nextQuestion,
  mayDecide,
  selectedQuestion,
  sendQuestionCard,
  parsePayload,
  detail,
  live,
  readAnswers,
  readQuestions,
} from "./pending-question.ts";
import type { Payload } from "./pending-question.ts";

type DecisionEvent = Extract<ChannelEvent, { kind: "decision" }>;
export class PendingManager {
  private readonly ctx: RuntimeContext;
  constructor(context: RuntimeContext) {
    this.ctx = context;
  }
  async request(
    event: AdapterEvent,
    turnId: string,
    chatId: string,
    userId: string,
    language: Language,
  ): Promise<void> {
    if (
      !event.requestId ||
      !event.turnId ||
      !this.ctx.adapter.capabilities.has("approve")
    )
      throw new Error("Approval request is incomplete or unsupported");
    const known = this.findKnown(
      event.sessionId,
      event.requestId,
      event.turnId,
    );
    if (known) {
      if (known.status === "pending") await this.show(known);
      return;
    }
    const turn = this.ctx.store.getTurn(turnId);
    if (!turn || !["submitting", "active", "uncertain"].includes(turn.status))
      throw new Error("Approval turn is not active");
    if (turn.externalTurnId && turn.externalTurnId !== event.turnId)
      throw new Error("Approval turn does not match");
    const id = this.createRequest(event, turnId, { chatId, userId, language });
    await this.show(this.ctx.store.getRequest(id));
  }
  private createRequest(
    event: AdapterEvent,
    turnId: string,
    target: { chatId: string; userId: string; language: Language },
  ): string {
    const id = randomUUID();
    const now = this.ctx.now();
    const text = detail(event);
    const payload: Payload = {
      sessionId: event.sessionId,
      requestId: event.requestId ?? "",
      chatId: target.chatId,
      userId: target.userId,
      language: target.language,
      externalTurnId: event.turnId ?? "",
      detail: text,
      summary: text.split("\n")[0] ?? text,
      questions:
        event.kind === "question-requested" ? readQuestions(event) : [],
    };
    const timeout = this.ctx.config.approval_timeout;
    this.ctx.store.createRequest({
      id,
      turnId,
      kind: event.kind === "question-requested" ? "question" : "approval",
      payload: JSON.stringify(payload),
      createdAt: now.toISOString(),
      expiresAt:
        timeout === null || event.kind === "question-requested"
          ? null
          : new Date(now.getTime() + timeout * 1000).toISOString(),
    });
    return id;
  }
  private find(
    sessionId: string,
    requestId: string,
  ): PendingRequest | undefined {
    return this.ctx.store.listRequests(["pending", "answering"]).find((row) => {
      const data = parsePayload(row);
      return data.sessionId === sessionId && data.requestId === requestId;
    });
  }
  private findKnown(
    sessionId: string,
    requestId: string,
    turnId: string,
  ): PendingRequest | undefined {
    return this.ctx.store
      .listRequests([
        "pending",
        "answering",
        "answered",
        "resolved_elsewhere",
        "stale",
        "expired",
        "cancelled",
      ])
      .find((row) => {
        const data = parsePayload(row);
        return (
          data.sessionId === sessionId &&
          data.requestId === requestId &&
          data.externalTurnId === turnId
        );
      });
  }
  private async show(row: PendingRequest | undefined): Promise<void> {
    if (!row) return;
    const data = parsePayload(row);
    if (row.kind === "question") await this.alignQuestionCard(row.id);
    const intent = transientIntent(
      `request/${row.id}`,
      row.kind,
      data.chatId,
      this.ctx.now(),
    );
    await this.ctx.transient.create(intent, () => this.sendCard(row, data));
  }
  private async alignQuestionCard(id: string): Promise<void> {
    const progress = this.ctx.store.getCursor(`question/${id}`) ?? "{}";
    const marker = `question-card/${id}`;
    if (this.ctx.store.getCursor(marker) === progress) return;
    await this.ctx.transient.remove(`request/${id}`);
    this.ctx.store.setCursor(marker, progress);
  }
  private sendCard(
    row: PendingRequest,
    data: Payload,
    silent = false,
  ): Promise<number> {
    if (row.kind === "question") {
      const answers = readAnswers(this.ctx.store, row.id);
      const question = nextQuestion(data.questions, answers);
      if (!question) throw new Error("Question is already complete");
      const questionIndex = data.questions.indexOf(question);
      return sendQuestionCard(
        this.ctx,
        data.chatId,
        row.id,
        questionIndex,
        question,
        silent,
      );
    }
    return sendApproval(
      this.ctx.channel,
      data.chatId,
      data.summary,
      data.detail,
      `p:${row.id}:a`,
      `p:${row.id}:d`,
      data.language,
      silent,
    );
  }
  async decision(event: DecisionEvent): Promise<boolean> {
    const parsed = /^p:([0-9a-f-]{36}):([ad]|q\d+-\d+)$/u.exec(event.data);
    if (!parsed?.[1] || !parsed[2]) return false;
    const row = this.ctx.store.getRequest(parsed[1]);
    if (row?.status !== "pending") {
      await this.ctx.channel.answerCallback(
        event.callbackId,
        catalog[event.language].stale,
      );
      return true;
    }
    const data = parsePayload(row);
    if (!mayDecide(this.ctx, row, event, data)) return this.stale(event);
    if (parsed[2].startsWith("q"))
      return this.questionDecision(event, row, data, parsed[2]);
    if (row.kind !== "approval") return this.stale(event);
    const decision = parsed[2] === "a" ? "accept" : "decline";
    if (
      !this.ctx.store.answerRequest(
        row.id,
        decision,
        this.ctx.now().toISOString(),
      )
    )
      return this.stale(event);
    await this.acknowledge(event.callbackId);
    await this.answer(row, data, decision, "answered");
    return true;
  }
  private async questionDecision(
    event: DecisionEvent,
    row: PendingRequest,
    data: Payload,
    choice: string,
  ): Promise<boolean> {
    const label = selectedQuestion(row, data, choice, this.ctx.store);
    if (!label) return this.stale(event);
    if (!(await this.applyQuestion(row, data, label, event.updateId)))
      return this.stale(event);
    await this.acknowledge(event.callbackId);
    return true;
  }
  private async stale(event: DecisionEvent): Promise<true> {
    await this.ctx.channel.answerCallback(
      event.callbackId,
      catalog[event.language].stale,
    );
    return true;
  }
  private async acknowledge(id: string): Promise<void> {
    try {
      await this.ctx.channel.answerCallback(id);
    } catch {
      this.ctx.log("callback_ack_failed", { code: "unconfirmed" });
    }
  }
  private async answer(
    row: PendingRequest,
    data: Payload,
    decision: unknown,
    completed: "answered" | "expired",
  ): Promise<void> {
    try {
      await this.ctx.adapter.approve({ requestId: data.requestId, decision });
      if (
        this.ctx.store.resolveRequest(
          row.id,
          "answering",
          completed,
          this.ctx.now().toISOString(),
        )
      )
        await this.ctx.transient.remove(`request/${row.id}`);
    } catch {
      this.ctx.log("approval_answer_uncertain", { code: "unconfirmed" });
    }
  }
  async textAnswer(
    event: Extract<ChannelEvent, { kind: "message" }>,
  ): Promise<boolean> {
    const candidates = this.ctx.store
      .listRequests(["pending"])
      .filter((row) => {
        if (row.kind !== "question") return false;
        const data = parsePayload(row);
        return (
          data.chatId === event.chatId &&
          data.userId === event.userId &&
          live(row, this.ctx)
        );
      });
    const row = candidates.length === 1 ? candidates[0] : undefined;
    if (!row || !event.text.trim()) return false;
    return this.applyQuestion(
      row,
      parsePayload(row),
      event.text,
      event.updateId,
    );
  }
  private async applyQuestion(
    row: PendingRequest,
    data: Payload,
    answer: string,
    updateId: number,
  ): Promise<boolean> {
    const answers = readAnswers(this.ctx.store, row.id);
    const question = nextQuestion(data.questions, answers);
    if (!question) return false;
    answers[question.id] = { answers: [answer] };
    const final = !nextQuestion(data.questions, answers);
    const result = { answers };
    if (
      !this.ctx.store.commitQuestionAnswer(
        updateId,
        row.id,
        JSON.stringify(answers),
        final ? JSON.stringify(result) : undefined,
        this.ctx.now().toISOString(),
      )
    )
      return false;
    if (final) await this.answer(row, data, result, "answered");
    else await this.show(row);
    return true;
  }
  async resolved(event: AdapterEvent): Promise<void> {
    if (!event.requestId) return;
    const row = this.find(event.sessionId, event.requestId);
    if (!row) return;
    if (
      this.ctx.store.resolveRequest(
        row.id,
        row.status === "answering" ? "answering" : "pending",
        "resolved_elsewhere",
        this.ctx.now().toISOString(),
      )
    )
      await this.ctx.transient.remove(`request/${row.id}`);
  }
  async recover(replayedRequestIds: ReadonlySet<string>): Promise<void> {
    for (const row of this.ctx.store.listRequests(["pending", "answering"])) {
      const data = parsePayload(row);
      if (replayedRequestIds.has(data.requestId)) {
        if (row.status === "pending") await this.show(row);
        continue;
      }
      this.ctx.store.setCursor(
        `request-interrupt/${row.id}`,
        data.externalTurnId,
      );
    }
    await this.retryInterrupts();
  }
  private async retryInterrupts(): Promise<void> {
    const interrupted = new Set<string>();
    for (const row of this.ctx.store.listRequests([
      "pending",
      "answering",
      "stale",
    ])) {
      const key = `request-interrupt/${row.id}`;
      const externalTurnId = this.ctx.store.getCursor(key);
      if (!externalTurnId) continue;
      if (
        row.status !== "stale" &&
        !this.ctx.store.resolveRequest(
          row.id,
          row.status === "answering" ? "answering" : "pending",
          "stale",
          this.ctx.now().toISOString(),
        )
      )
        continue;
      await this.ctx.transient.remove(`request/${row.id}`);
      if (live(row, this.ctx) && !interrupted.has(externalTurnId)) {
        try {
          await this.ctx.adapter.interrupt(externalTurnId);
        } catch {
          this.ctx.log("stale_interrupt_failed", { code: "unconfirmed" });
          continue;
        }
        interrupted.add(externalTurnId);
      }
      this.ctx.store.setCursor(key, "");
    }
  }
  async tick(): Promise<void> {
    await this.retryInterrupts();
    const now = this.ctx.now();
    for (const row of this.ctx.store.listRequests(["pending"])) {
      if (row.expiresAt && row.expiresAt <= now.toISOString()) {
        if (this.ctx.store.answerRequest(row.id, "decline", now.toISOString()))
          await this.answer(row, parsePayload(row), "decline", "expired");
        continue;
      }
      await this.show(row);
      await this.ctx.transient.repost(`request/${row.id}`, now, () =>
        this.sendCard(row, parsePayload(row), true),
      );
    }
  }
  async cancelTurn(turnId: string): Promise<void> {
    for (const row of this.ctx.store.listRequests(["pending", "answering"])) {
      if (row.turnId !== turnId) continue;
      if (
        this.ctx.store.resolveRequest(
          row.id,
          row.status === "answering" ? "answering" : "pending",
          "cancelled",
          this.ctx.now().toISOString(),
        )
      )
        await this.ctx.transient.remove(`request/${row.id}`);
    }
  }
}
