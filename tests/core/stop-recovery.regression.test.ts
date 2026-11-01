import { afterEach, expect, it, vi } from "vitest";
import { TelegramChannel, TransientManager } from "../../src/channels/index.js";
import { Delivery } from "../../src/core/delivery.js";
import { TurnEngine } from "../../src/core/engine.js";
import { turnDraftId } from "../../src/core/turn-draft.js";
import { collectMessages, recordEvent } from "../../src/core/queue.js";
import { setup } from "./stop-status-fixture.js";

afterEach(() => vi.useRealTimers());

it("clears an older cancelled preview before submitting queued work on recovery", async () => {
  const f = setup();
  let resumed: TurnEngine | undefined;
  try {
    await f.progress();
    await vi.advanceTimersByTimeAsync(1);
    await f.engine.close();
    f.store.setCursor(`user-stop/${f.id}`, "1");
    f.store.transitionTurn(f.id, "active", "cancelled");
    recordEvent(f.context, {
      ...f.command,
      kind: "message",
      updateId: 2,
      text: "queued next",
      attachments: [],
    });
    collectMessages(f.context);
    f.context.channel = new TelegramChannel({
      token: "123:synthetic",
      allowedUserIds: [1],
      fetch: f.api.fetch,
    });
    f.context.transient = new TransientManager(f.context.channel, f.store);
    f.context.adapter.submit = vi.fn().mockImplementation(async () => {
      f.api.calls.push("submit-next");
      await vi.advanceTimersByTimeAsync(1);
      return { turnId: "next" };
    });
    f.api.calls.length = 0;
    resumed = new TurnEngine(f.context, new Delivery(f.context));
    await resumed.tick();
    expect(f.api.calls.indexOf("sendRichMessage")).toBeLessThan(
      f.api.calls.indexOf("submit-next"),
    );
    expect(f.api.visible).toBe(true);
    expect(f.api.messages.size).toBe(0);
  } finally {
    await resumed?.close();
    f.store.close();
  }
});

it("clears a status update that applies after the native stop button hid the draft", async () => {
  const f = setup();
  let stopped: Promise<void> | undefined;
  try {
    await f.progress();
    await vi.advanceTimersByTimeAsync(1);
    f.api.holdDraft = true;
    await vi.advanceTimersByTimeAsync(20000);
    expect(f.api.release).toBeTypeOf("function");
    f.api.visible = false;
    stopped = f.engine.accept(
      [
        {
          ...f.command,
          kind: "generation-stopped",
          messageId: turnDraftId({ id: f.id, createdAt: "unused" }),
        },
      ],
      2,
    );
    await vi.advanceTimersByTimeAsync(1);
    expect(f.interrupt).toHaveBeenCalledTimes(1);
    f.api.release?.();
    await stopped;
    expect(f.api.visible).toBe(false);
    expect(f.api.messages.size).toBe(0);
  } finally {
    f.api.release?.();
    await stopped;
    await f.engine.close();
    f.store.close();
  }
});

it("retains and retries cancelled-preview cleanup after Telegram retry_after", async () => {
  const f = setup();
  try {
    await f.progress();
    await vi.advanceTimersByTimeAsync(1);
    f.api.finalFailures = 1;
    await f.engine.accept([f.command], 2);
    expect(f.api.visible).toBe(true);
    expect(f.store.getCursor(`status-draft/${f.id}`)).toBe("1");
    const attempts = f.api.calls.filter(
      (method) => method === "sendRichMessage",
    ).length;
    await f.engine.tick();
    expect(
      f.api.calls.filter((method) => method === "sendRichMessage"),
    ).toHaveLength(attempts);
    await vi.advanceTimersByTimeAsync(2000);
    await f.engine.tick();
    expect(f.api.visible).toBe(false);
    expect(f.api.messages.size).toBe(0);
    expect(f.store.getCursor(`status-draft/${f.id}`)).toBe("");
    expect(f.interrupt).toHaveBeenCalledTimes(1);
    expect(f.store.listOutbox(["pending", "sent"])).toHaveLength(0);
  } finally {
    await f.engine.close();
    f.store.close();
  }
});
