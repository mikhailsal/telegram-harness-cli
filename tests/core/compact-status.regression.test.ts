import { afterEach, expect, it, vi } from "vitest";
import { setup } from "./stop-status-fixture.js";

afterEach(() => vi.useRealTimers());

async function verifyNoticeCleanup(f: ReturnType<typeof setup>): Promise<void> {
  await f.engine.event({
    kind: "turn-completed",
    sessionId: "s",
    turnId: "compact",
    cursor: "replay",
    data: { status: "completed" },
  });
  expect(f.api.messages.size).toBe(1);
  await f.engine.accept([f.command], 2);
  expect(f.api.messages.size).toBe(0);
  await f.engine.event({
    kind: "turn-completed",
    sessionId: "s",
    turnId: "compact",
    cursor: "replay-after-cleanup",
    data: { status: "completed" },
  });
  expect(f.api.messages.size).toBe(0);
}

it("confirms completed compaction once and removes the notice on the next request", async () => {
  const f = setup();
  const draft = vi.spyOn(f.context.channel, "draft");
  try {
    f.store.transitionTurn(f.id, "active", "completed", { result: "" });
    await f.engine.event({
      kind: "turn-started",
      sessionId: "s",
      turnId: "compact",
      cursor: "start",
    });
    await f.engine.event({
      kind: "progress",
      sessionId: "s",
      turnId: "compact",
      cursor: "compact-progress",
      data: { kind: "compacting" },
    });
    await vi.advanceTimersByTimeAsync(1);
    expect(draft.mock.lastCall?.[2]).toContain("Compacting context");
    expect(draft.mock.lastCall?.[2]).not.toContain("terminal");
    expect(f.api.visible).toBe(true);
    await f.engine.event({
      kind: "turn-completed",
      sessionId: "s",
      turnId: "compact",
      cursor: "complete",
      data: { status: "completed" },
    });
    expect(f.api.visible).toBe(false);
    expect(f.api.messages.size).toBe(1);
    expect([...f.api.messages.values()][0]).toMatchObject({
      text: "Context compacted.",
    });
    await verifyNoticeCleanup(f);
    expect(f.store.listOutbox(["pending", "sent"])).toHaveLength(0);
  } finally {
    await f.engine.close();
    f.store.close();
  }
});

it("keeps a normal answer when the Telegram turn compacts its own context", async () => {
  const f = setup();
  try {
    await f.engine.event({
      kind: "progress",
      sessionId: "s",
      turnId: "turn",
      correlationId: f.id,
      cursor: "auto-compact",
      data: { kind: "compacting" },
    });
    await vi.advanceTimersByTimeAsync(1);
    await f.engine.event({
      kind: "turn-completed",
      sessionId: "s",
      turnId: "turn",
      correlationId: f.id,
      cursor: "complete",
      text: "answer",
      data: { status: "completed" },
    });
    expect(f.store.listOutbox(["sent"])).toHaveLength(1);
    expect(f.api.messages.size).toBe(1);
    expect(f.api.visible).toBe(false);
  } finally {
    await f.engine.close();
    f.store.close();
  }
});

it.each(["failed", "interrupted"])(
  "does not confirm %s compaction",
  async (status) => {
    const f = setup();
    try {
      f.store.transitionTurn(f.id, "active", "completed", { result: "" });
      await f.engine.event({
        kind: "turn-started",
        sessionId: "s",
        turnId: "compact",
        cursor: "start",
      });
      await f.engine.event({
        kind: "progress",
        sessionId: "s",
        turnId: "compact",
        cursor: "progress",
        data: { kind: "compacting" },
      });
      expect(f.api.messages.size).toBe(0);
      await f.engine.event({
        kind: "turn-completed",
        sessionId: "s",
        turnId: "compact",
        cursor: "done",
        data: { status },
      });
      expect([...f.api.messages.values()]).not.toContainEqual(
        expect.objectContaining({ text: "Context compacted." }),
      );
    } finally {
      await f.engine.close();
      f.store.close();
    }
  },
);

it("repairs a completed compaction after a crash before notification", async () => {
  const f = setup();
  try {
    f.store.transitionTurn(f.id, "active", "completed", { result: "" });
    await f.engine.event({
      kind: "turn-started",
      sessionId: "s",
      turnId: "compact",
      cursor: "start",
    });
    await f.engine.event({
      kind: "progress",
      sessionId: "s",
      turnId: "compact",
      cursor: "progress",
      data: { kind: "compacting" },
    });
    const turn = f.store
      .listTurns(["active"])
      .find((item) => item.externalTurnId === "compact");
    expect(turn).toBeDefined();
    if (!turn) throw new Error("missing compaction turn");
    f.store.transitionTurn(turn.id, "active", "completed", { result: "" });
    await f.engine.event({
      kind: "turn-completed",
      sessionId: "s",
      turnId: "compact",
      cursor: "done",
      data: { status: "completed" },
    });
    expect([...f.api.messages.values()][0]).toMatchObject({
      text: "Context compacted.",
    });
  } finally {
    await f.engine.close();
    f.store.close();
  }
});
