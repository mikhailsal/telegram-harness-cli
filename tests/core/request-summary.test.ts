import { expect, it, vi } from "vitest";
import { join } from "node:path";
import { openStore } from "../../src/store/index.js";
import { queueRequestSummaries } from "../../src/core/request-summary.js";
import type { RuntimeContext } from "../../src/core/context.js";

const at = "2026-01-01T00:10:00.000Z";
function setup(path = ":memory:") {
  const store = openStore(path);
  store.putEntity({ id: "e", directory: "/work", botId: "1", createdAt: at });
  store.createTurn({
    id: "t",
    entityId: "e",
    source: "telegram",
    createdAt: at,
  });
  const ctx = {
    store,
    config: { timezone: "Europe/Moscow" },
    now: () => new Date(at),
  } as RuntimeContext;
  return { store, ctx };
}
function create(
  store: ReturnType<typeof openStore>,
  id: string,
  kind: "approval" | "question" = "approval",
): void {
  store.createRequest({
    id,
    turnId: "t",
    kind,
    createdAt: at,
    payload: JSON.stringify({
      chatId: "1",
      language: "en",
      detail: "npm install example",
      questions: [{ id: "q", question: "Which option?" }],
    }),
  });
}

it("records question and automatic decisions with local decision time and no outbox", () => {
  const { store, ctx } = setup();
  try {
    create(store, "q", "question");
    store.setCursor(
      "question/q",
      JSON.stringify({ q: { answers: ["Option A"] } }),
    );
    store.answerRequest("q", "answer", at);
    store.resolveRequest("q", "answering", "answered", "2026-01-01T00:12:00Z");
    create(store, "expired");
    store.answerRequest("expired", "decline", at);
    store.resolveRequest("expired", "answering", "expired", at);
    create(store, "external");
    store.resolveRequest("external", "pending", "resolved_elsewhere", at);
    const write = vi.spyOn(store, "recordDecisionHistory");
    queueRequestSummaries(ctx);
    queueRequestSummaries(ctx);
    expect(write).toHaveBeenCalledTimes(3);
    const entries = store.messagesForTurn("t");
    expect(entries).toHaveLength(3);
    expect(entries.find((row) => row.id === "request-summary/q")?.text).toBe(
      "You answered: Which option? — Option A · 03:10",
    );
    expect(
      entries.find((row) => row.id === "request-summary/expired")?.text,
    ).toBe("Expired, declined: npm install example · 03:10");
    expect(
      entries.find((row) => row.id === "request-summary/external")?.text,
    ).toBe("Resolved in terminal: npm install example · 03:10");
    expect(store.searchMessages("Which option", 10).messages).toHaveLength(1);
    expect(
      store.listOutbox(["pending", "sending", "sent", "failed"]),
    ).toHaveLength(0);
  } finally {
    store.close();
  }
});

it("does not duplicate a decision history entry after restart", async () => {
  const { mkdtemp, rm } = await import("node:fs/promises");
  const { tmpdir } = await import("node:os");
  const directory = await mkdtemp(join(tmpdir(), "thc-decision-history-"));
  const path = join(directory, "store.sqlite");
  try {
    const first = setup(path);
    create(first.store, "restart");
    first.store.answerRequest("restart", "accept", at);
    first.store.resolveRequest("restart", "answering", "answered", at);
    queueRequestSummaries(first.ctx);
    first.store.close();
    const restored = openStore(path);
    try {
      const ctx = {
        store: restored,
        config: { timezone: "UTC" },
        now: () => new Date("2026-01-02T00:00:00Z"),
      } as RuntimeContext;
      queueRequestSummaries(ctx);
      const entries = restored.searchMessages("npm install", 10).messages;
      expect(entries).toHaveLength(1);
      expect(entries[0]?.sentAt).toBe(at);
      expect(entries[0]?.text).toContain("03:10");
    } finally {
      restored.close();
    }
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

it("omits stale and cancelled requests and supports old records without a timestamp", () => {
  const { store, ctx } = setup();
  try {
    for (const [index, status] of (["stale", "cancelled"] as const).entries()) {
      create(store, status);
      store.resolveRequest(status, "pending", status, at);
      store.enqueueOutbox({
        id: `request-summary/${status}`,
        turnId: "t",
        chunk: -1 - index,
        chatId: "1",
        text: "legacy notice",
        createdAt: at,
      });
    }
    create(store, "legacy");
    store.answerRequest("legacy", "decline", at);
    store.resolveRequest("legacy", "answering", "answered", at);
    store.setCursor("request-resolved-at/legacy", "");
    queueRequestSummaries(ctx);
    const entries = store.messagesForTurn("t");
    expect(entries).toHaveLength(1);
    expect(entries[0]?.text).toBe("You denied: npm install example · 03:10");
    expect(entries[0]?.sentAt).toBe(at);
    expect(
      store.listOutbox(["pending", "sending", "sent", "failed"]),
    ).toHaveLength(0);
  } finally {
    store.close();
  }
});

it("removes a legacy pending summary without touching the final answer", () => {
  const { store, ctx } = setup();
  try {
    create(store, "old");
    store.answerRequest("old", "accept", at);
    store.resolveRequest("old", "answering", "answered", at);
    store.enqueueOutbox({
      id: "request-summary/old",
      turnId: "t",
      chunk: -1,
      chatId: "1",
      text: "legacy notice",
      createdAt: at,
    });
    store.enqueueOutbox({
      id: "answer",
      turnId: "t",
      chunk: 0,
      chatId: "1",
      text: "final answer",
      createdAt: at,
    });
    queueRequestSummaries(ctx);
    expect(store.getOutbox("request-summary/old")).toBeUndefined();
    expect(store.getOutbox("answer")?.status).toBe("pending");
    expect(store.searchMessages("npm install", 10).messages).toHaveLength(1);
  } finally {
    store.close();
  }
});

it("does not put a fallback harness item identifier into history", () => {
  const { store, ctx } = setup();
  try {
    store.createRequest({
      id: "opaque",
      turnId: "t",
      kind: "approval",
      createdAt: at,
      payload: JSON.stringify({
        chatId: "1",
        language: "en",
        detail: "call-ABC123",
      }),
    });
    store.answerRequest("opaque", "accept", at);
    store.resolveRequest("opaque", "answering", "answered", at);
    queueRequestSummaries(ctx);
    expect(store.messagesForTurn("t")[0]?.text).toBe("You allowed · 03:10");
  } finally {
    store.close();
  }
});
