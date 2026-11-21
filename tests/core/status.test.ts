import { afterEach, expect, it, vi } from "vitest";
import { parseConfig } from "../../src/core/config.js";
import type { RuntimeContext } from "../../src/core/context.js";
import { TurnStatuses } from "../../src/core/status.js";
import { openStore } from "../../src/store/index.js";

afterEach(() => vi.useRealTimers());

function setup() {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-01-01T00:00:00Z"));
  const store = openStore(":memory:");
  const now = () => new Date();
  const draft = vi.fn().mockResolvedValue(undefined);
  const ctx = {
    store,
    now,
    entityId: "entity",
    adapterReady: true,
    config: parseConfig({
      token: { env: "TOKEN" },
      allowlist: [1],
      sandbox: "read-only",
      stall_timeout: 5,
    }),
    channel: { draft, hasDraft: () => false },
    transient: { remove: vi.fn().mockResolvedValue(undefined) },
  } as unknown as RuntimeContext;
  store.putEntity({
    id: "entity",
    directory: "/tmp/example",
    botId: "1",
    createdAt: now().toISOString(),
  });
  store.setCursor("chat-id", "1");
  const id = "a0000000-0000-4000-8000-000000000001";
  store.createTurn({
    id,
    entityId: "entity",
    source: "telegram",
    createdAt: now().toISOString(),
  });
  store.transitionTurn(id, "queued", "active", {
    externalTurnId: "external",
    updatedAt: now().toISOString(),
  });
  const statuses = new TurnStatuses(ctx);
  return { store, now, draft, ctx, id, statuses };
}

it("renders ephemeral progress, queue, stall and recovery states", async () => {
  const { store, now, draft, ctx, id, statuses } = setup();
  try {
    statuses.event({
      kind: "progress",
      sessionId: "s",
      turnId: "external",
      cursor: "p",
      data: { kind: "thinking", detail: "Investigating the issue" },
    });
    await vi.advanceTimersByTimeAsync(1);
    expect(draft.mock.lastCall?.[2]).toContain(
      "Thinking: Investigating the issue",
    );
    // protects=no hint before stall_timeout; fails_when=the hint shows on a live turn; no_claim=none beyond protects
    expect(draft.mock.lastCall?.[2]).not.toContain("/stop");
    store.createTurn({
      id: "b0000000-0000-4000-8000-000000000002",
      entityId: "entity",
      source: "telegram",
      createdAt: now().toISOString(),
    });
    await vi.advanceTimersByTimeAsync(5000);
    statuses.refresh();
    await vi.advanceTimersByTimeAsync(1000);
    // protects=a stalled status tells the user how to interrupt; fails_when=the hint is missing or follows the queue line; no_claim=Telegram rendering of the draft (channel is a mock)
    expect(draft.mock.lastCall?.[2]).toMatch(
      / · No updates for \S+\n\/stop interrupts the task\nWaiting: 1$/,
    );
    ctx.adapterReady = false;
    statuses.refresh();
    await vi.advanceTimersByTimeAsync(1000);
    expect(draft.mock.lastCall?.[2]).toContain("reconnecting");
    ctx.adapterReady = true;
    await statuses.acknowledge();
    await vi.advanceTimersByTimeAsync(1000);
    expect(draft.mock.lastCall?.[2]).toContain("Follow-up received");
    expect(JSON.stringify(store.getTurn(id))).not.toContain(
      "Investigating the issue",
    );
  } finally {
    await statuses.close();
    store.close();
  }
});

it("recovers the status, reflects decisions, and restores work when resolved", async () => {
  const { store, draft, ctx, id, statuses, now } = setup();
  try {
    statuses.refresh();
    await vi.advanceTimersByTimeAsync(1);
    expect(draft.mock.lastCall?.[2]).toContain("Thinking");
    store.createRequest({
      id: "request",
      turnId: id,
      kind: "approval",
      payload: "{}",
      createdAt: now().toISOString(),
    });
    statuses.refresh();
    await vi.advanceTimersByTimeAsync(1000);
    expect(draft.mock.lastCall?.[2]).toContain("Waiting for your decision");
    store.answerRequest("request", "accept");
    store.resolveRequest("request", "answering", "answered");
    statuses.refresh();
    await vi.advanceTimersByTimeAsync(1000);
    expect(draft.mock.lastCall?.[2]).toContain("Thinking");
    store.setCursor("deferred-new", "1");
    statuses.refresh();
    await vi.advanceTimersByTimeAsync(1000);
    expect(draft.mock.lastCall?.[2]).toContain("new conversation");
    ctx.config.status = "off";
    statuses.refresh();
  } finally {
    await statuses.close();
    store.close();
  }
});

it("limits tool and reasoning text in transient status", async () => {
  const { store, draft, statuses } = setup();
  try {
    for (const [kind, detail, prefix] of [
      ["command", "x".repeat(80), "Running"],
      ["read", "src/example.ts", "Reading"],
      ["file-change", "", "Editing files"],
      ["thinking", "x".repeat(80), "Thinking"],
    ]) {
      statuses.event({
        kind: "progress",
        sessionId: "s",
        turnId: "external",
        cursor: kind ?? "",
        data: { kind, detail },
      });
      await vi.advanceTimersByTimeAsync(1000);
      expect(draft.mock.lastCall?.[2]).toContain(prefix);
      expect(String(draft.mock.lastCall?.[2])).not.toContain("x".repeat(61));
    }
  } finally {
    await statuses.close();
    store.close();
  }
});

it("removes cancelled statuses and shows submission for the next turn", async () => {
  const { store, draft, statuses, id, now } = setup();
  try {
    statuses.event({
      kind: "progress",
      sessionId: "s",
      cursor: "p",
      data: { kind: "unknown" },
    });
    await vi.advanceTimersByTimeAsync(1);
    store.setCursor(`user-stop/${id}`, "1");
    await statuses.cancelled();
    const sent = draft.mock.calls.length;
    statuses.refresh();
    await vi.advanceTimersByTimeAsync(1000);
    expect(draft.mock.calls).toHaveLength(sent);
    store.transitionTurn(id, "active", "cancelled");
    await statuses.cancelled();
    const next = "c0000000-0000-4000-8000-000000000003";
    store.createTurn({
      id: next,
      entityId: "entity",
      source: "telegram",
      createdAt: now().toISOString(),
    });
    store.transitionTurn(next, "queued", "submitting");
    statuses.refresh();
    await vi.advanceTimersByTimeAsync(1);
    expect(draft.mock.lastCall?.[2]).toContain("Sending to agent");
  } finally {
    await statuses.close();
    store.close();
  }
});

it("shows the stop hint in Russian when the turn stalls", async () => {
  const { store, draft, statuses } = setup();
  try {
    store.setCursor("language", "ru");
    statuses.refresh();
    await vi.advanceTimersByTimeAsync(5000);
    statuses.refresh();
    await vi.advanceTimersByTimeAsync(1000);
    // protects=Russian stalled status carries the Russian hint at the threshold (>=); fails_when=hint missing or English; no_claim=Telegram rendering (channel is a mock)
    expect(draft.mock.lastCall?.[2]).toMatch(
      / · Нет новостей \S+\n\/stop прервёт задачу$/,
    );
  } finally {
    await statuses.close();
    store.close();
  }
});

it("labels a continued terminal turn as continuing, not as a terminal task", async () => {
  const { store, now, draft, statuses, id } = setup();
  try {
    store.transitionTurn(id, "active", "completed");
    const labels: string[] = [];
    for (const linked of [false, true]) {
      const turn = `d000000${linked ? "2" : "1"}-0000-4000-8000-000000000004`;
      store.createTurn({
        id: turn,
        entityId: "entity",
        source: "terminal",
        createdAt: now().toISOString(),
      });
      store.transitionTurn(turn, "queued", "active", {
        updatedAt: now().toISOString(),
      });
      if (linked) store.setCursor(`continues/${turn}`, id);
      statuses.refresh();
      await vi.advanceTimersByTimeAsync(1);
      labels.push(String(draft.mock.lastCall?.[2]));
      store.transitionTurn(turn, "active", "completed");
    }
    // protects=a continuation shows the continuing label and a plain terminal task keeps its own; fails_when=the label ignores the continues link; no_claim=Telegram rendering (channel is a mock)
    expect(labels[0]).toContain("terminal task");
    expect(labels[1]).toContain("Continuing the task");
  } finally {
    await statuses.close();
    store.close();
  }
});

it("names the model, effort and fast tier Telegram sent on the first status line", async () => {
  const { store, draft, statuses, id } = setup();
  try {
    const record = (values: Record<string, string>) => {
      store.setCursor(`turn-execution/${id}/submit`, JSON.stringify(values));
    };
    record({ model: "beta", effort: "low", serviceTier: "priority" });
    statuses.refresh();
    await vi.advanceTimersByTimeAsync(1);
    // protects=the live status proves which settings the turn was sent with; fails_when=the label is missing or lands after the stall or queue lines; no_claim=Telegram rendering (channel is a mock)
    expect(String(draft.mock.lastCall?.[2]).split("\n")[0]).toMatch(
      /^💭 Thinking \S+ · beta · effort low · fast$/u,
    );
    store.setCursor("language", "ru");
    record({ model: "none", effort: "high", serviceTier: "standard" });
    statuses.refresh();
    await vi.advanceTimersByTimeAsync(1000);
    expect(draft.mock.lastCall?.[2]).toMatch(/ · глубина high$/u);
    // A turn sent with Codex's own settings, or a damaged record, shows none.
    for (const raw of [
      JSON.stringify({ model: "none", effort: "none", serviceTier: "none" }),
      "{broken",
    ]) {
      store.setCursor(`turn-execution/${id}/submit`, raw);
      statuses.refresh();
      await vi.advanceTimersByTimeAsync(1000);
      expect(draft.mock.lastCall?.[2]).toMatch(/^💭 Думаю \S+$/u);
    }
  } finally {
    await statuses.close();
    store.close();
  }
});
