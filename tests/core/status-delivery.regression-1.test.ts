import { afterEach, expect, it, vi } from "vitest";
import {
  ChannelStatus,
  TelegramChannel,
  TransientManager,
} from "../../src/channels/index.js";
import { parseConfig } from "../../src/core/config.js";
import type { RuntimeContext } from "../../src/core/context.js";
import { Delivery } from "../../src/core/delivery.js";
import { TurnEngine } from "../../src/core/engine.js";
import { openStore } from "../../src/store/index.js";

afterEach(() => vi.useRealTimers());

class StatusApi {
  readonly calls: string[] = [];
  readonly applied: string[] = [];
  readonly messages = new Map<number, string>();
  readonly failAnswers = new Set<number>();
  draftVisible = false;
  holdDraft = false;
  answerAttempts = 0;
  retryAfter = 1;
  release: (() => void) | undefined;
  private nextId = 100;
  readonly fetch: typeof fetch = (input, init) => {
    const url =
      typeof input === "string"
        ? input
        : input instanceof URL
          ? input.href
          : input.url;
    const method = url.split("/").at(-1) ?? "";
    const body = JSON.parse(
      typeof init?.body === "string" ? init.body : "{}",
    ) as Record<string, unknown>;
    this.calls.push(method);
    if (method === "sendRichMessageDraft") {
      const apply = () => {
        this.draftVisible = true;
        this.applied.push("draft");
        return Response.json({ ok: true, result: true });
      };
      if (!this.holdDraft) return Promise.resolve(apply());
      this.holdDraft = false;
      return new Promise<Response>((resolve) => {
        this.release = () => {
          this.release = undefined;
          resolve(apply());
        };
      });
    }
    if (method === "sendRichMessage") return Promise.resolve(this.answer());
    if (method === "sendMessage") {
      const id = ++this.nextId;
      this.messages.set(id, String(body.text));
      return Promise.resolve(
        Response.json({ ok: true, result: { message_id: id } }),
      );
    }
    if (method === "deleteMessage")
      this.messages.delete(Number(body.message_id));
    return Promise.resolve(Response.json({ ok: true, result: true }));
  };
  private answer(): Response {
    this.answerAttempts++;
    if (this.failAnswers.delete(this.answerAttempts))
      return Response.json({
        ok: false,
        error_code: 429,
        description: "wait",
        parameters: { retry_after: this.retryAfter },
      });
    this.draftVisible = false;
    this.applied.push("answer");
    const id = ++this.nextId;
    this.messages.set(id, "answer");
    return Response.json({ ok: true, result: { message_id: id } });
  }
}

function seedStatusTurn(
  store: ReturnType<typeof openStore>,
  now: () => Date,
): string {
  store.putEntity({
    id: "e",
    directory: "/tmp/status-test",
    botId: "123",
    createdAt: now().toISOString(),
  });
  const id = "a0000000-0000-4000-8000-000000000001";
  store.createTurn({
    id,
    entityId: "e",
    source: "telegram",
    createdAt: now().toISOString(),
  });
  store.transitionTurn(id, "queued", "active", { externalTurnId: "turn" });
  store.setCursor("chat-id", "1");
  return id;
}
function setup(mode: "draft" | "message" = "draft") {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-31T12:00:00Z"));
  const api = new StatusApi();
  const store = openStore(":memory:");
  const now = () => new Date();
  const id = seedStatusTurn(store, now);
  const channel = new TelegramChannel({
    token: "123:synthetic",
    allowedUserIds: [1],
    fetch: api.fetch,
  });
  const context = {
    store,
    now,
    channel,
    entityId: "e",
    sessionId: "s",
    adapterReady: true,
    config: parseConfig({
      token: { env: "TOKEN" },
      allowlist: [1],
      sandbox: "read-only",
      status: mode,
    }),
    transient: new TransientManager(channel, store, now),
    adapter: { capabilities: new Set() },
    persistAdapter: () => undefined,
    log: () => undefined,
  } as unknown as RuntimeContext;
  const engine = new TurnEngine(context, new Delivery(context));
  const progress = () =>
    engine.event({
      kind: "progress",
      sessionId: "s",
      turnId: "turn",
      correlationId: id,
      cursor: "progress",
      data: { kind: "thinking" },
    });
  const complete = (text = "done") =>
    engine.event({
      kind: "turn-completed",
      sessionId: "s",
      turnId: "turn",
      correlationId: id,
      cursor: "completed",
      text,
    });
  return { api, store, engine, progress, complete };
}

// Regression: a status request completing after the answer recreates its draft.
// Found by the owner phone QA on 2026-11-01.
it.each(Array.from({ length: 8 }, (_, index) => index + 1))(
  "drains a delayed draft before final delivery (forced ordering %s)",
  async () => {
    const f = setup();
    f.api.holdDraft = true;
    let completion: Promise<void> | undefined;
    try {
      await f.progress();
      await vi.advanceTimersByTimeAsync(1);
      expect(f.api.release).toBeTypeOf("function");
      completion = f.complete();
      await vi.advanceTimersByTimeAsync(1);
      const attemptsBeforeDraft = f.api.answerAttempts;
      const pendingBeforeDraft = f.store.listOutbox(["pending"]).length;
      f.api.release?.();
      await completion;
      expect(f.api.applied).toEqual(["draft", "answer"]);
      expect(attemptsBeforeDraft).toBe(0);
      expect(pendingBeforeDraft).toBe(1);
      expect(f.api.draftVisible).toBe(false);
      await vi.advanceTimersByTimeAsync(40000);
      expect(f.api.applied).toEqual(["draft", "answer"]);
      expect(f.store.listOutbox(["sent"])).toHaveLength(1);
    } finally {
      f.api.release?.();
      await completion;
      await f.engine.close();
      f.store.close();
    }
  },
);

it("keeps draft heartbeat alive while a failed first send waits for retry", async () => {
  const f = setup();
  f.api.failAnswers.add(1);
  f.api.retryAfter = 30;
  try {
    await f.progress();
    await vi.advanceTimersByTimeAsync(1);
    await f.complete();
    const before = f.api.applied.length;
    await vi.advanceTimersByTimeAsync(1000);
    await f.engine.tick();
    await vi.advanceTimersByTimeAsync(20000);
    expect(f.api.applied.length).toBeGreaterThan(before);
    expect(f.api.answerAttempts).toBe(1);
    await vi.advanceTimersByTimeAsync(10000);
    await f.engine.tick();
    expect(f.api.draftVisible).toBe(false);
    expect(f.store.listOutbox(["sent"])).toHaveLength(1);
  } finally {
    await f.engine.close();
    f.store.close();
  }
});

it("keeps status paused after a visible first chunk when a later chunk retries", async () => {
  const f = setup();
  f.api.failAnswers.add(2);
  try {
    await f.progress();
    await vi.advanceTimersByTimeAsync(1);
    await f.complete(`${"a".repeat(20000)}\n\n${"b".repeat(20000)}`);
    expect(f.store.listOutbox(["sent"])).toHaveLength(1);
    expect(f.store.listOutbox(["pending"])).toHaveLength(1);
    const afterFirst = f.api.applied.length;
    await vi.advanceTimersByTimeAsync(20000);
    expect(f.api.applied.length).toBe(afterFirst);
    expect(f.api.draftVisible).toBe(false);
    await f.engine.tick();
    expect(f.store.listOutbox(["sent"])).toHaveLength(2);
  } finally {
    await f.engine.close();
    f.store.close();
  }
});

it("retains a fallback status until every final chunk is delivered", async () => {
  const f = setup("message");
  f.api.failAnswers.add(2);
  try {
    await f.progress();
    await vi.advanceTimersByTimeAsync(1);
    const statusId = f.store.listTransients()[0]?.messageId;
    expect(statusId).toBeTypeOf("number");
    await f.complete(`${"a".repeat(20000)}\n\n${"b".repeat(20000)}`);
    expect(f.store.listTransients()).toHaveLength(1);
    expect(f.api.messages.has(statusId ?? 0)).toBe(true);
    await vi.advanceTimersByTimeAsync(2000);
    await f.engine.tick();
    expect(f.api.messages.has(statusId ?? 0)).toBe(false);
    expect(f.store.listTransients()).toHaveLength(0);
  } finally {
    await f.engine.close();
    f.store.close();
  }
});

it("holds later updates without timers while paused and resumes with the latest text", async () => {
  vi.useFakeTimers();
  const api = new StatusApi();
  const channel = new TelegramChannel({
    token: "123:synthetic",
    allowedUserIds: [1],
    fetch: api.fetch,
  });
  const draft = vi.spyOn(channel, "draft");
  const status = new ChannelStatus(channel, 1, 42);
  try {
    status.update("first");
    await vi.advanceTimersByTimeAsync(1000);
    await status.pause();
    status.update("later");
    expect(vi.getTimerCount()).toBe(0);
    await vi.advanceTimersByTimeAsync(40000);
    expect(draft).toHaveBeenCalledTimes(1);
    status.resume();
    await vi.advanceTimersByTimeAsync(1000);
    expect(draft).toHaveBeenLastCalledWith(1, 42, "later");
  } finally {
    await status.stop();
  }
});
