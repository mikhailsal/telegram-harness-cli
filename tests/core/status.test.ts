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
    store.createTurn({
      id: "b0000000-0000-4000-8000-000000000002",
      entityId: "entity",
      source: "telegram",
      createdAt: now().toISOString(),
    });
    await vi.advanceTimersByTimeAsync(5000);
    statuses.refresh();
    await vi.advanceTimersByTimeAsync(1000);
    expect(draft.mock.lastCall?.[2]).toContain("No updates");
    expect(draft.mock.lastCall?.[2]).toContain("Waiting: 1");
    ctx.adapterReady = false;
    statuses.refresh();
    await vi.advanceTimersByTimeAsync(1000);
    expect(draft.mock.lastCall?.[2]).toContain("reconnecting");
    ctx.adapterReady = true;
    statuses.acknowledge();
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
