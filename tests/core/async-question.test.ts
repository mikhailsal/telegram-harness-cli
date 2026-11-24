import { expect, it } from "vitest";
import type { AdapterEvent } from "../../src/adapters/contract.js";
import { lastDelivery, message, setup } from "./continuation-fixture.js";

// Codex lists the options in the text as Markdown; a list adds nothing here.
const question = "Cats or dogs?";
function asyncOutput(cursor: string, text = question): AdapterEvent {
  return {
    kind: "output",
    sessionId: "thread",
    turnId: "turn-1",
    cursor,
    text,
    data: { delivery: "async" },
  };
}

it("sends a mid-turn question at once and keeps the final answer last", async () => {
  const { engine, store, deliverChunk } = setup();
  try {
    await engine.accept([message(7, "ask me something")], 8);
    await engine.event(asyncOutput("turn-1/item/call_1"));
    // protects=a request_user_input_async question reaches Telegram while the turn runs, with a notification; fails_when=it waits for the turn to end or is overwritten by the final answer; no_claim=Telegram rendering (channel is a fake)
    expect(deliverChunk).toHaveBeenCalledTimes(1);
    expect(lastDelivery(deliverChunk)).toEqual({
      plain: question,
      options: expect.objectContaining({ silent: false }) as unknown,
    });
    await engine.event(asyncOutput("turn-1/item/call_1"));
    // protects=a replayed question is not sent twice; fails_when=reconnect history resends it; no_claim=none beyond protects
    expect(deliverChunk).toHaveBeenCalledTimes(1);
    await engine.event({
      kind: "output",
      sessionId: "thread",
      turnId: "turn-1",
      cursor: "turn-1/item/msg_1",
      text: "Asked; waiting for your answer.",
    });
    await engine.event({
      kind: "turn-completed",
      sessionId: "thread",
      turnId: "turn-1",
      cursor: "turn-1/complete",
    });
    // protects=the turn's final answer is the last ordinary message, not the question; fails_when=the question replaces the final answer; no_claim=none beyond protects
    expect(lastDelivery(deliverChunk)).toEqual({
      plain: "Asked; waiting for your answer.",
      options: expect.objectContaining({ replyToMessageId: 7 }) as unknown,
    });
    const turn = store.listTurns(["completed"])[0];
    // protects=the question is part of the chat history the bridge keeps (the fake clock gives both one timestamp, so order is not checked here); fails_when=it is sent without being recorded; no_claim=none beyond protects
    expect(
      store
        .messagesForTurn(turn?.id ?? "")
        .filter((row) => row.author === "assistant")
        .map((row) => row.text)
        .sort(),
    ).toEqual(["Asked; waiting for your answer.", question]);
  } finally {
    await engine.close();
    store.close();
  }
});

it("does not resend a finished turn's question from replayed history", async () => {
  const { engine, store, deliverChunk } = setup();
  try {
    await engine.accept([message(7, "ask me something")], 8);
    await engine.event({
      kind: "turn-completed",
      sessionId: "thread",
      turnId: "turn-1",
      cursor: "turn-1/complete",
      text: "Done",
    });
    expect(deliverChunk).toHaveBeenCalledTimes(1);
    await engine.event(asyncOutput("turn-1/item/call_old"));
    // protects=history replayed after an upgrade or reconnect does not post old questions; fails_when=a completed turn's async item is queued; no_claim=none beyond protects
    expect(deliverChunk).toHaveBeenCalledTimes(1);
  } finally {
    await engine.close();
    store.close();
  }
});

// Value: protects=an async question in a Regular-format chat is sent as an html row; fails_when=messageFormat is dropped from queueAsyncOutput or rowFormat mis-maps html; why_new=the only async test uses the default Rich format; seam=none; no_claim=Telegram rendering (fake channel)
it("sends a question in a Regular-format chat as an html row", async () => {
  const { engine, store } = setup();
  try {
    store.setCursor("message-format/1", "regular");
    await engine.accept([message(7, "ask me something")], 8);
    await engine.event(asyncOutput("turn-1/item/call_1"));
    // protects=the question row is html, not rich; fails_when=the chat format is ignored for async output; no_claim=none beyond protects
    const rows = store
      .listOutbox(["pending", "sending", "sent"])
      .filter((row) => row.chunk < 0);
    expect(rows.map((row) => row.format)).toEqual(["html"]);
  } finally {
    await engine.close();
    store.close();
  }
});

// Value: protects=a whitespace-only async text is not sent; fails_when=the trim guard in queueAsyncOutput is removed; why_new=nothing covers the blank-text path in core; seam=none; no_claim=none beyond protects
it("ignores a blank async question", async () => {
  const { engine, store, deliverChunk } = setup();
  try {
    await engine.accept([message(7, "ask me something")], 8);
    await engine.event(asyncOutput("turn-1/item/blank", "  \n "));
    // protects=no empty message is queued or sent; fails_when=the blank text reaches the outbox; no_claim=none beyond protects
    expect(deliverChunk).not.toHaveBeenCalled();
    expect(store.listOutbox(["pending", "sending", "sent"])).toEqual([]);
  } finally {
    await engine.close();
    store.close();
  }
});

// Value: protects=a multi-chunk question gets distinct negative chunk numbers below an earlier mid-turn row; fails_when=chunk numbering collides or restarts; why_new=only single-chunk questions are tested; seam=none; no_claim=none
it("numbers a multi-chunk question below earlier mid-turn rows", async () => {
  const { engine, store } = setup();
  try {
    store.setCursor("message-format/1", "regular");
    await engine.accept([message(7, "ask me something")], 8);
    await engine.event(asyncOutput("turn-1/item/a"));
    const long = Array.from({ length: 4 }, () => "x".repeat(3000)).join("\n\n");
    await engine.event(asyncOutput("turn-1/item/b", long));
    // protects=every row of the turn has its own negative chunk, the long question after the first; fails_when=numbers repeat or turn positive; no_claim=none beyond protects
    const chunks = store
      .listOutbox(["pending", "sending", "sent"])
      .map((row) => row.chunk)
      .sort((a, b) => b - a);
    expect(chunks.length).toBeGreaterThan(2);
    expect(new Set(chunks).size).toBe(chunks.length);
    expect(chunks.every((chunk) => chunk < 0)).toBe(true);
  } finally {
    await engine.close();
    store.close();
  }
});
