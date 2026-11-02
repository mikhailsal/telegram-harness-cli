import { afterEach, expect, it, vi } from "vitest";
import { setup } from "./stop-status-fixture.js";
import { turnDraftId } from "../../src/core/turn-draft.js";

afterEach(() => vi.useRealTimers());

it("replaces Thinking with the final Rich content before persisting the same answer", async () => {
  const f = setup();
  const frames: { method: string; body: unknown }[] = [];
  f.context.channel.bot.api.config.use((previous, method, payload, signal) => {
    if (method === "sendRichMessageDraft" || method === "sendRichMessage")
      frames.push({ method, body: payload });
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
      text: "**Final answer**",
    });
    expect(frames.map((frame) => frame.method)).toEqual([
      "sendRichMessageDraft",
      "sendRichMessageDraft",
      "sendRichMessage",
    ]);
    expect(frames[1]?.body).toMatchObject({
      draft_id: turnDraftId({ id: f.id, createdAt: "" }),
      rich_message: { markdown: "**Final answer**" },
      can_stop: false,
    });
    expect(frames[2]?.body).toMatchObject({
      rich_message: { markdown: "**Final answer**" },
    });
    expect(f.api.messages.size).toBe(1);
    expect(f.api.calls).not.toContain("deleteMessage");
    expect(f.store.listOutbox(["sent"])[0]?.attempts).toBe(1);
    const count = frames.length;
    await vi.advanceTimersByTimeAsync(40000);
    await f.engine.tick();
    expect(frames).toHaveLength(count);
  } finally {
    await f.engine.close();
    f.store.close();
  }
});

it("sends Rich directly when no native preview was published", async () => {
  const f = setup();
  try {
    await f.engine.event({
      kind: "turn-completed",
      sessionId: "s",
      turnId: "turn",
      correlationId: f.id,
      cursor: "done",
      text: "answer",
    });
    expect(f.api.calls).toEqual(["sendRichMessage"]);
    expect(f.store.listOutbox(["sent"])).toHaveLength(1);
  } finally {
    await f.engine.close();
    f.store.close();
  }
});

it.each([400, 429])(
  "preserves final delivery when the handoff receives %s",
  async (code) => {
    const f = setup();
    let rejected = false;
    f.context.channel.bot.api.config.use(
      (previous, method, payload, signal) => {
        if (
          method === "sendRichMessageDraft" &&
          "can_stop" in payload &&
          !payload.can_stop &&
          !rejected
        ) {
          rejected = true;
          return Promise.resolve({
            ok: false,
            error_code: code,
            description: "handoff rejected",
            parameters: { retry_after: 2 },
          });
        }
        return previous(method, payload, signal);
      },
    );
    try {
      await f.progress();
      await vi.advanceTimersByTimeAsync(1);
      await f.engine.event({
        kind: "turn-completed",
        sessionId: "s",
        turnId: "turn",
        correlationId: f.id,
        cursor: "done",
        text: "answer",
      });
      expect(rejected).toBe(true);
      if (code === 429) {
        expect(f.api.messages.size).toBe(0);
        expect(f.store.listOutbox(["pending"])).toHaveLength(1);
        await vi.advanceTimersByTimeAsync(2000);
        await f.engine.tick();
      }
      expect(f.api.messages.size).toBe(1);
      expect(
        f.api.calls.filter((method) => method === "sendRichMessage"),
      ).toHaveLength(1);
      expect(f.store.listOutbox(["sent"])).toHaveLength(1);
      expect(f.api.calls).not.toContain("deleteMessage");
    } finally {
      await f.engine.close();
      f.store.close();
    }
  },
);
