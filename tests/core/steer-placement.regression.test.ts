import { afterEach, expect, it, vi } from "vitest";
import { setup } from "./stop-status-fixture.js";
import { SteerRejectedError } from "../../src/adapters/contract.js";
import type { ChannelEvent } from "../../src/channels/index.js";

afterEach(() => vi.useRealTimers());

function placeAnswer(order: (number | "draft")[], result: unknown): void {
  if (
    !result ||
    typeof result !== "object" ||
    !("message_id" in result) ||
    typeof result.message_id !== "number"
  )
    return;
  const index = order.indexOf("draft");
  if (index >= 0) order.splice(index, 1, result.message_id);
  else order.push(result.message_id);
}
function trackOrder(
  channel: ReturnType<typeof setup>["context"]["channel"],
  order: (number | "draft")[],
): void {
  channel.bot.api.config.use(async (prev, method, payload, signal) => {
    const result = await prev(method, payload, signal);
    if (!result.ok) return result;
    if (method === "sendRichMessageDraft" && !order.includes("draft"))
      order.push("draft");
    if (method === "sendRichMessage") placeAnswer(order, result.result);
    if (method === "deleteMessage" && "message_id" in payload) {
      const index = order.indexOf(payload.message_id);
      if (index >= 0) order.splice(index, 1);
    }
    return result;
  });
}

function placement() {
  const f = setup();
  const order: (number | "draft")[] = [1];
  trackOrder(f.context.channel, order);
  f.store.recordVisibleMessage({
    id: "inbound/1",
    chatId: "1",
    telegramMessageId: 1,
    author: "user",
    sentAt: new Date().toISOString(),
    text: "first",
    turnId: f.id,
    possiblyDuplicated: false,
  });
  const followup: ChannelEvent = {
    ...f.command,
    kind: "message",
    updateId: 2,
    messageId: 2,
    text: "follow-up",
    attachments: [],
  };
  const steer = vi.fn().mockResolvedValue(undefined);
  f.context.adapter.steer = steer;
  (f.context.adapter.capabilities as Set<string>).add("steer");
  f.store.setCursor("followup-mode", "steer");
  const complete = () =>
    f.engine.event({
      kind: "turn-completed",
      sessionId: "s",
      turnId: "turn",
      correlationId: f.id,
      cursor: "done",
      text: "answer",
      data: { status: "completed" },
    });
  return { ...f, order, followup, steer, complete };
}

it("places a steered answer after its accepted follow-up rather than at the parent's old preview", async () => {
  const f = placement();
  try {
    await f.progress();
    await vi.advanceTimersByTimeAsync(1);
    f.order.push(2);
    await f.engine.accept([f.followup], 3);
    await vi.advanceTimersByTimeAsync(1000);
    await f.complete();
    const answerId = f.store.listOutbox(["sent"])[0]?.messageId;
    expect(f.steer).toHaveBeenCalledTimes(1);
    expect(f.order).toEqual([1, 2, answerId]);
    expect(f.api.messages.size).toBe(1);
    expect(
      f.store.listTransients().filter((row) => row.kind === "status"),
    ).toHaveLength(0);
  } finally {
    await f.engine.close();
    f.store.close();
  }
});

it("keeps the existing preview for queued messages and rejected steering", async () => {
  const f = placement();
  try {
    await f.progress();
    await vi.advanceTimersByTimeAsync(1);
    f.steer.mockRejectedValue(new SteerRejectedError());
    f.order.push(2);
    await f.engine.accept([f.followup], 3);
    expect(f.order).toEqual([1, "draft", 2]);
    expect(
      f.api.calls.filter((method) => method === "sendRichMessage"),
    ).toHaveLength(0);
  } finally {
    await f.engine.close();
    f.store.close();
  }
});

it("does not deliver at the old position when moving the preview is rate limited", async () => {
  const f = placement();
  try {
    await f.progress();
    await vi.advanceTimersByTimeAsync(1);
    f.api.finalFailures = 1;
    f.order.push(2);
    await f.engine.accept([f.followup], 3);
    await f.complete();
    expect(f.store.listOutbox(["sent"])).toHaveLength(0);
    expect(f.store.listOutbox(["pending"])).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(2000);
    await f.engine.tick();
    const answerId = f.store.listOutbox(["sent"])[0]?.messageId;
    expect(f.order).toEqual([1, 2, answerId]);
    expect(f.api.messages.size).toBe(1);
  } finally {
    await f.engine.close();
    f.store.close();
  }
});
