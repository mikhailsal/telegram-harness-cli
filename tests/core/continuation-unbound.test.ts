import { expect, it, vi } from "vitest";
import type { TurnEngine } from "../../src/core/engine.js";
import { lastDelivery, message, setup } from "./continuation-fixture.js";

// Regression (#10): while a Telegram submission had no Codex turn ID yet, the
// turn-started event of a daemon-recovery turn was deferred as if it might be
// the submission's own turn. Its rootTurnId was never applied, the submission
// could adopt the recovery's turn ID, and the recovery's answer went to the
// wrong turn. Value: protects=a turn-started that continues another turn is
// applied at once, linked to the turn it continues, and never offered to the
// unbound submission, which still binds its own turn; fails_when=the recovery is
// deferred, the submission adopts its turn ID, the recovery answer is not a
// reply to the original message, or the submission loses its own pending turn
// ID; why_new=continuation.test.ts only covers recoveries with no unbound
// submission; seam=none; no_claim=that Codex orders these events this way (the
// adapter emits them; the engine is driven directly)

async function interruptedThenUnbound(engine: TurnEngine): Promise<void> {
  await engine.accept([message(7, "review my PR")], 8);
  await engine.event({
    kind: "turn-completed",
    sessionId: "thread",
    turnId: "turn-1",
    cursor: "turn-1/complete",
    data: { status: "interrupted" },
  });
  // The next message is submitted, but Codex has not named its turn yet.
  await engine.accept([message(9, "and check the tests")], 10);
}
function recoveryStarted(engine: TurnEngine): Promise<void> {
  return engine.event({
    kind: "turn-started",
    sessionId: "thread",
    turnId: "recovery",
    cursor: "recovery/start",
    data: { rootTurnId: "turn-1" },
  });
}
function submissionInput(engine: TurnEngine, id: string): Promise<void> {
  return engine.event({
    kind: "input",
    sessionId: "thread",
    correlationId: id,
    cursor: `input/${id}`,
    text: "and check the tests",
  });
}

it("links a recovery that starts while a Telegram submission is unbound and answers the original message", async () => {
  const { engine, store, deliverChunk, context } = setup();
  context.adapter.submit = vi
    .fn()
    .mockResolvedValueOnce({ turnId: "turn-1" })
    .mockResolvedValue({});
  try {
    await interruptedThenUnbound(engine);
    const [pending] = store.listTurns(["submitting"]);
    expect(pending?.externalTurnId ?? null).toBeNull();
    await recoveryStarted(engine);
    await submissionInput(engine, pending?.id ?? "");
    // The answer arrives as output; the completion carries no text, so the
    // delivered answer is the stored output.
    await engine.event({
      kind: "output",
      sessionId: "thread",
      turnId: "recovery",
      cursor: "recovery/output",
      text: "Review done",
    });
    await engine.event({
      kind: "turn-completed",
      sessionId: "thread",
      turnId: "recovery",
      cursor: "recovery/complete",
    });
    const submission = store.getTurn(pending?.id ?? "");
    expect(submission?.externalTurnId ?? null).not.toBe("recovery");
    expect(submission?.status).toBe("active");
    const recovery = store
      .listTurns(["completed"])
      .find((turn) => turn.externalTurnId === "recovery");
    const original = store
      .listTurns(["interrupted", "failed", "completed", "cancelled"])
      .find((turn) => turn.externalTurnId === "turn-1");
    expect(store.getCursor(`continues/${recovery?.id ?? ""}`)).toBe(
      original?.id,
    );
    expect(lastDelivery(deliverChunk)).toEqual({
      plain: "Review done",
      options: expect.objectContaining({ replyToMessageId: 7 }) as unknown,
    });
  } finally {
    await engine.close();
    store.close();
  }
});

it("keeps the submission's own pending turn when a recovery starts in between", async () => {
  const { engine, store, context } = setup();
  context.adapter.submit = vi
    .fn()
    .mockResolvedValueOnce({ turnId: "turn-1" })
    .mockResolvedValue({});
  try {
    await interruptedThenUnbound(engine);
    const [pending] = store.listTurns(["submitting"]);
    await engine.event({
      kind: "turn-started",
      sessionId: "thread",
      turnId: "turn-2",
      cursor: "turn-2/start",
    });
    await recoveryStarted(engine);
    await submissionInput(engine, pending?.id ?? "");
    expect(store.getTurn(pending?.id ?? "")?.externalTurnId).toBe("turn-2");
  } finally {
    await engine.close();
    store.close();
  }
});
