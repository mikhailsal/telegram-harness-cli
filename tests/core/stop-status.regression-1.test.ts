import { afterEach, expect, it, vi } from "vitest";
import { TelegramChannel, TransientManager } from "../../src/channels/index.js";
import { Delivery } from "../../src/core/delivery.js";
import { TurnEngine } from "../../src/core/engine.js";
import { turnDraftId } from "../../src/core/turn-draft.js";
import { setup } from "./stop-status-fixture.js";

afterEach(() => vi.useRealTimers());

it("closes a native preview on typed stop without persisting an answer or acknowledgement", async () => {
  const f = setup();
  try {
    await f.progress();
    await vi.advanceTimersByTimeAsync(1);
    expect(f.api.visible).toBe(true);
    await f.engine.accept([f.command], 2);
    expect(f.interrupt).toHaveBeenCalledWith("turn");
    expect(f.api.visible).toBe(false);
    expect(f.api.messages.size).toBe(0);
    expect(f.api.calls).toContain("sendRichMessage");
    expect(f.store.listTransients()).toHaveLength(0);
    expect(f.store.listOutbox(["pending", "sending", "sent"])).toHaveLength(0);
    await f.engine.event({
      kind: "turn-completed",
      sessionId: "s",
      turnId: "turn",
      cursor: "cancelled",
      text: "racing answer",
      data: { status: "aborted" },
    });
    expect(f.store.getTurn(f.id)?.status).toBe("cancelled");
    expect(f.store.getTurn(f.id)?.result).toBe("");
    expect(f.store.messagesForTurn(f.id)).toHaveLength(0);
    const count = f.api.calls.length;
    await vi.advanceTimersByTimeAsync(40000);
    expect(f.api.calls).toHaveLength(count);
  } finally {
    await f.engine.close();
    f.store.close();
  }
});

it.each(Array.from({ length: 8 }, (_, i) => i + 1))(
  "interrupts before waiting for a delayed preview, then clears it (ordering %s)",
  async () => {
    const f = setup();
    f.api.holdDraft = true;
    let stopped: Promise<void> | undefined;
    try {
      await f.progress();
      await vi.advanceTimersByTimeAsync(1);
      stopped = f.engine.accept([f.command], 2);
      await vi.advanceTimersByTimeAsync(1);
      expect(f.interrupt).toHaveBeenCalledWith("turn");
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
  },
);

it("leaves running work's native preview alone on graceful bridge shutdown", async () => {
  const f = setup();
  try {
    await f.progress();
    await vi.advanceTimersByTimeAsync(1);
    await f.engine.close();
    expect(f.api.visible).toBe(true);
    expect(f.interrupt).not.toHaveBeenCalled();
    expect(f.api.calls).not.toContain("sendRichMessage");
  } finally {
    f.store.close();
  }
});

it("does not interrupt twice when the native button arrives after typed stop", async () => {
  const f = setup();
  try {
    await f.progress();
    await vi.advanceTimersByTimeAsync(1);
    await f.engine.accept([f.command], 2);
    await f.engine.accept(
      [
        {
          ...f.command,
          kind: "generation-stopped",
          updateId: 2,
          messageId: turnDraftId({ id: f.id, createdAt: "unused" }),
        },
      ],
      3,
    );
    expect(f.interrupt).toHaveBeenCalledTimes(1);
    expect(f.api.messages.size).toBe(0);
  } finally {
    await f.engine.close();
    f.store.close();
  }
});

it("honors a native stop without leaving an answer or acknowledgement", async () => {
  const f = setup();
  try {
    await f.progress();
    await vi.advanceTimersByTimeAsync(1);
    f.api.visible = false;
    await f.engine.accept(
      [
        {
          ...f.command,
          kind: "generation-stopped",
          messageId: turnDraftId({ id: f.id, createdAt: "unused" }),
        },
      ],
      2,
    );
    expect(f.interrupt).toHaveBeenCalledTimes(1);
    expect(f.api.messages.size).toBe(0);
    await vi.advanceTimersByTimeAsync(40000);
    expect(f.api.visible).toBe(false);
  } finally {
    await f.engine.close();
    f.store.close();
  }
});

it("ignores a delayed native stop for a different turn with the same UUID prefix", async () => {
  const f = setup();
  try {
    await f.progress();
    await vi.advanceTimersByTimeAsync(1);
    const oldId = "a0000000-0000-4000-8000-000000000002";
    await f.engine.accept(
      [
        {
          ...f.command,
          kind: "generation-stopped",
          messageId: turnDraftId({ id: oldId, createdAt: "unused" }),
        },
      ],
      2,
    );
    expect(f.interrupt).not.toHaveBeenCalled();
    expect(f.store.getCursor(`user-stop/${f.id}`)).not.toBe("1");
    expect(f.api.visible).toBe(true);
  } finally {
    await f.engine.close();
    f.store.close();
  }
});

it("clears a fresh native draft as well as a fallback persisted by an older process", async () => {
  const f = setup();
  try {
    f.context.transient.registerExisting(
      {
        id: `status/${f.id}`,
        kind: "status",
        chatId: "1",
        createdAt: new Date().toISOString(),
        expiresAt: null,
      },
      90,
    );
    f.api.messages.set(90, "old fallback");
    await f.progress();
    await vi.advanceTimersByTimeAsync(1);
    await f.engine.accept([f.command], 2);
    expect(f.api.visible).toBe(false);
    expect(f.api.messages.size).toBe(0);
  } finally {
    await f.engine.close();
    f.store.close();
  }
});

it("clears a surviving preview when stop is the first input after bridge restart", async () => {
  const f = setup();
  let resumed: TurnEngine | undefined;
  try {
    await f.progress();
    await vi.advanceTimersByTimeAsync(1);
    await f.engine.close();
    f.context.channel = new TelegramChannel({
      token: "123:synthetic",
      allowedUserIds: [1],
      fetch: f.api.fetch,
    });
    f.context.transient = new TransientManager(f.context.channel, f.store);
    resumed = new TurnEngine(f.context, new Delivery(f.context));
    await resumed.accept([f.command], 2);
    expect(f.interrupt).toHaveBeenCalledTimes(1);
    expect(f.api.visible).toBe(false);
    expect(f.api.messages.size).toBe(0);
    expect(f.store.getCursor(`status-draft/${f.id}`)).toBe("");
  } finally {
    await resumed?.close();
    f.store.close();
  }
});

it("recovers a preview after a stored cancellation even without a new Telegram command", async () => {
  const f = setup();
  let resumed: TurnEngine | undefined;
  try {
    await f.progress();
    await vi.advanceTimersByTimeAsync(1);
    await f.engine.close();
    f.store.setCursor(`user-stop/${f.id}`, "1");
    f.store.transitionTurn(f.id, "active", "cancelled");
    f.context.channel = new TelegramChannel({
      token: "123:synthetic",
      allowedUserIds: [1],
      fetch: f.api.fetch,
    });
    f.context.transient = new TransientManager(f.context.channel, f.store);
    resumed = new TurnEngine(f.context, new Delivery(f.context));
    await resumed.tick();
    expect(f.api.visible).toBe(false);
    expect(f.api.messages.size).toBe(0);
    expect(f.interrupt).not.toHaveBeenCalled();
  } finally {
    await resumed?.close();
    f.store.close();
  }
});
