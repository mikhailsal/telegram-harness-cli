import { afterEach, expect, it, vi } from "vitest";
import { setup } from "./stop-status-fixture.js";

afterEach(() => vi.useRealTimers());

it("keeps one visible answer and stops status updates while its format conversion retries", async () => {
  const f = setup();
  f.store.setCursor("message-format/1", "regular");
  let edits = 0;
  f.context.channel.bot.api.config.use((previous, method, payload, signal) => {
    if (method === "editMessageText" && ++edits === 1)
      return Promise.resolve({
        ok: false,
        error_code: 429,
        description: "wait",
        parameters: { retry_after: 2 },
      });
    return previous(method, payload, signal);
  });
  try {
    await f.progress();
    await vi.advanceTimersByTimeAsync(1);
    await f.engine.event({
      kind: "turn-completed",
      sessionId: "s",
      turnId: "turn",
      correlationId: f.id,
      cursor: "done",
      text: "**Done**",
    });
    const row = f.store.listOutbox(["pending"])[0];
    expect(row?.messageId).toBeTypeOf("number");
    expect(f.api.visible).toBe(false);
    expect(f.api.messages.size).toBe(1);
    const calls = [...f.api.calls];
    await vi.advanceTimersByTimeAsync(2000);
    expect(f.api.calls).toEqual(calls);
    await f.engine.tick();
    expect(edits).toBe(2);
    expect(f.store.listOutbox(["sent"])[0]?.messageId).toBe(row?.messageId);
    expect(
      f.api.calls.filter((method) => method === "sendRichMessage"),
    ).toHaveLength(1);
    expect(f.api.calls).not.toContain("deleteMessage");
    expect(f.api.calls).not.toContain("sendMessage");
    const delivered = [...f.api.calls];
    await vi.advanceTimersByTimeAsync(40000);
    await f.engine.tick();
    expect(f.api.calls).toEqual(delivered);
  } finally {
    await f.engine.close();
    f.store.close();
  }
});
