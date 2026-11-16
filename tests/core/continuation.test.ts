import { expect, it, vi } from "vitest";
import type { TurnEngine } from "../../src/core/engine.js";
import {
  lastDelivery,
  message,
  recover,
  setup,
} from "./continuation-fixture.js";

it("reports a Codex interruption truthfully and answers the continuation under the user's message", async () => {
  const { engine, store, deliverChunk } = setup();
  try {
    await engine.accept([message(7, "review my PR")], 8);
    await engine.event({
      kind: "turn-completed",
      sessionId: "thread",
      turnId: "turn-1",
      cursor: "turn-1/complete",
      data: { status: "interrupted" },
    });
    // protects=a harness-reported interruption names Codex, not the bridge; fails_when=the old "bridge restarted" text is used; no_claim=Telegram rendering (channel is a fake)
    expect(lastDelivery(deliverChunk).plain).toBe(
      "⚠️ Codex interrupted the task",
    );
    await engine.event({
      kind: "turn-started",
      sessionId: "thread",
      turnId: "recovery",
      cursor: "recovery/start",
      data: { rootTurnId: "turn-1" },
    });
    await engine.event({
      kind: "turn-completed",
      sessionId: "thread",
      turnId: "recovery",
      cursor: "recovery/complete",
      text: "Review done",
    });
    // protects=the recovery answer is a reply to the original message without the terminal marker; fails_when=it is shown as an unrelated 🖥 terminal task; no_claim=that Codex emits rootTurnId (adapter tests cover parsing)
    expect(lastDelivery(deliverChunk)).toEqual({
      plain: "Review done",
      options: expect.objectContaining({ replyToMessageId: 7 }) as unknown,
    });
    const recovery = store
      .listTurns(["completed"])
      .find((turn) => turn.externalTurnId === "recovery");
    expect(recovery?.source).toBe("terminal");
    expect(store.getCursor(`continues/${recovery?.id ?? ""}`)).toBeTruthy();
  } finally {
    await engine.close();
    store.close();
  }
});

it("keeps the bridge restart wording for turns the bridge lost", async () => {
  const { engine, store, deliverChunk } = setup();
  try {
    await engine.accept([message(7, "review my PR")], 8);
    const turn = store.listTurns(["active", "submitting", "uncertain"])[0];
    await engine.event({
      kind: "turn-completed",
      sessionId: "thread",
      cursor: `lost/${turn?.id ?? ""}`,
      correlationId: turn?.id ?? "",
      data: { status: "interrupted" },
    });
    // protects=the supervisor's own restart path still says the bridge restarted; fails_when=it uses the Codex wording; no_claim=supervisor detection of lost turns (event injected directly)
    expect(lastDelivery(deliverChunk).plain).toBe(
      "⚠️ Task interrupted when the bridge restarted",
    );
  } finally {
    await engine.close();
    store.close();
  }
});

it("leaves a turn whose root is not a Telegram turn as a terminal task", async () => {
  const { engine, store, deliverChunk } = setup();
  try {
    await engine.event({
      kind: "turn-started",
      sessionId: "thread",
      turnId: "local",
      cursor: "local/start",
      data: { rootTurnId: "unknown-root" },
    });
    await engine.event({
      kind: "turn-completed",
      sessionId: "thread",
      turnId: "local",
      cursor: "local/complete",
      text: "local answer",
    });
    // protects=only a matching Telegram root links a continuation; fails_when=an unrelated chain loses its 🖥 marker; no_claim=none beyond protects
    expect(lastDelivery(deliverChunk).plain).toBe("🖥 local answer");
    expect(
      store.getCursor(
        "continues/" + (store.listTurns(["completed"])[0]?.id ?? ""),
      ),
    ).toBeUndefined();
  } finally {
    await engine.close();
    store.close();
  }
});

it("answers the continuation under the latest accepted steer of the original turn", async () => {
  const { engine, store, deliverChunk, context } = setup();
  context.adapter.steer = vi.fn().mockResolvedValue(undefined);
  (context.adapter.capabilities as Set<string>).add("steer");
  store.setCursor("followup-mode", "steer");
  try {
    await engine.accept([message(7, "review my PR")], 8);
    await engine.accept([message(8, "also check tests")], 9);
    await recover(engine);
    // protects=a continuation inherits the reply rule of the turn it continues, including accepted steers; fails_when=it replies to the first message instead of the latest steer; no_claim=steer submission itself (adapter is a fake)
    expect(lastDelivery(deliverChunk).options).toEqual(
      expect.objectContaining({ replyToMessageId: 8 }),
    );
  } finally {
    await engine.close();
    store.close();
  }
});

it("delivers the continuation to the chat of the original turn", async () => {
  const { engine, store, deliverChunk } = setup();
  try {
    await engine.accept([message(7, "review my PR")], 8);
    store.setCursor("chat-id", "2");
    await recover(engine);
    // protects=a continuation goes to the chat that asked, not the most recent chat; fails_when=another allowlisted user's chat receives the answer; no_claim=multi-user admission (chat cursor set directly)
    expect(deliverChunk.mock.calls.at(-1)?.[0]).toBe("1");
  } finally {
    await engine.close();
    store.close();
  }
});

function seedTelegramTurn(
  store: ReturnType<typeof setup>["store"],
  id: string,
  externalTurnId: string,
  sessionId = "session",
) {
  store.createTurn({
    id,
    entityId: "entity",
    source: "telegram",
    createdAt: "2026-11-15T07:40:00.000Z",
  });
  store.transitionTurn(id, "queued", "active", {
    externalTurnId,
    sessionId,
    updatedAt: "2026-11-15T07:40:00.000Z",
  });
  store.transitionTurn(id, "active", "completed");
}
function start(engine: TurnEngine, turnId: string, rootTurnId: string) {
  return engine.event({
    kind: "turn-started",
    sessionId: "thread",
    turnId,
    cursor: `${turnId}/start`,
    data: { rootTurnId },
  });
}
function linkOf(store: ReturnType<typeof setup>["store"], external: string) {
  const turn = store
    .listTurns(["active", "completed"])
    .find((row) => row.externalTurnId === external);
  return store.getCursor(`continues/${turn?.id ?? ""}`);
}

it("links a continuation to the original turn rather than its accepted steer", async () => {
  const { engine, store } = setup();
  try {
    // The steer sorts before the root, so a plain first match would pick it.
    seedTelegramTurn(store, "b-root", "turn-1");
    seedTelegramTurn(store, "a-steer", "turn-1");
    store.setCursor("followup-of/a-steer", "b-root");
    await start(engine, "recovery", "turn-1");
    // protects=the continuation anchors on the original turn, not its steer peer; fails_when=the followup-of preference is dropped and the earliest candidate wins; no_claim=reply or chat routing (covered by the steer test)
    expect(linkOf(store, "recovery")).toBe("b-root");
  } finally {
    await engine.close();
    store.close();
  }
});

it("does not link a root that belongs to another session", async () => {
  const { engine, store } = setup();
  try {
    store.putSession({
      id: "old",
      entityId: "entity",
      harness: "codex",
      externalId: "old-thread",
      startedAt: "2026-11-14T07:40:00.000Z",
      endedAt: "2026-11-14T08:40:00.000Z",
    });
    seedTelegramTurn(store, "old-root", "turn-1", "old");
    await start(engine, "recovery", "turn-1");
    // protects=a turn id reused in a rotated session never captures a terminal turn; fails_when=the sessionId filter is dropped; no_claim=session rotation itself
    expect(linkOf(store, "recovery")).toBeUndefined();
  } finally {
    await engine.close();
    store.close();
  }
});

it("never links a Telegram turn and never re-links a linked one", async () => {
  const { engine, store } = setup();
  try {
    seedTelegramTurn(store, "root-a", "root-a-ext");
    seedTelegramTurn(store, "root-b", "root-b-ext");
    await engine.accept([message(7, "review my PR")], 8);
    await start(engine, "turn-1", "root-a-ext");
    await start(engine, "recovery", "root-a-ext");
    await start(engine, "recovery", "root-b-ext");
    // protects=only a terminal turn is linked and its first link sticks; fails_when=a Telegram turn gets a continues link or a later start event moves the link; no_claim=none beyond protects
    expect(linkOf(store, "turn-1")).toBeUndefined();
    expect(linkOf(store, "recovery")).toBe("root-a");
  } finally {
    await engine.close();
    store.close();
  }
});
