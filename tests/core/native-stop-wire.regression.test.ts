import { afterEach, expect, it, vi } from "vitest";
import { TelegramChannel } from "../../src/channels/index.js";
import { turnDraftId } from "../../src/core/turn-draft.js";
import { setup } from "./stop-status-fixture.js";

afterEach(() => vi.useRealTimers());
function channelFor(draftId: unknown) {
  const fetcher: typeof fetch = () =>
    Promise.resolve(
      Response.json({
        ok: true,
        result: [
          {
            update_id: 41,
            stopped_message_generation: {
              chat: { id: 1, type: "private" },
              draft_id: draftId,
            },
          },
        ],
      }),
    );
  return new TelegramChannel({
    token: "123:synthetic",
    allowedUserIds: [1],
    fetch: fetcher,
  });
}
it("normalizes Telegram's decimal-string draft ID and interrupts the matching turn", async () => {
  const f = setup();
  try {
    const id = turnDraftId({ id: f.id, createdAt: "unused" });
    expect(id).toBeGreaterThan(2 ** 32);
    const batch = await channelFor(String(id)).poll(0);
    expect(batch.nextOffset).toBe(42);
    expect(batch.events).toMatchObject([
      { kind: "generation-stopped", messageId: id },
    ]);
    await f.engine.accept(batch.events, batch.nextOffset);
    expect(f.interrupt).toHaveBeenCalledExactlyOnceWith("turn");
    expect(f.store.getCursor(`user-stop/${f.id}`)).toBe("1");
  } finally {
    await f.engine.close();
    f.store.close();
  }
});
it.each([
  0,
  -1,
  1.5,
  Number.MAX_SAFE_INTEGER + 1,
  "",
  "0",
  "-1",
  "1.5",
  "1e3",
  " 1",
  "9007199254740992",
  null,
  {},
  true,
])(
  "ignores invalid draft ID %s while advancing the update offset",
  async (id) => {
    const batch = await channelFor(id).poll(0);
    expect(batch.nextOffset).toBe(42);
    expect(batch.events).toEqual([]);
  },
);
it("accepts a safe numeric draft ID and rejects a valid stale string at the engine boundary", async () => {
  const f = setup();
  try {
    const id = turnDraftId({ id: f.id, createdAt: "unused" });
    expect((await channelFor(id).poll(0)).events[0]).toMatchObject({
      kind: "generation-stopped",
      messageId: id,
    });
    const stale = await channelFor(String(id + 1)).poll(0);
    await f.engine.accept(stale.events, stale.nextOffset);
    expect(f.interrupt).not.toHaveBeenCalled();
  } finally {
    await f.engine.close();
    f.store.close();
  }
});
it.each([
  1,
  Number.MAX_SAFE_INTEGER,
  "1",
  "00042",
  String(Number.MAX_SAFE_INTEGER),
])("accepts valid decimal draft ID %s", async (id) => {
  const batch = await channelFor(id).poll(0);
  expect(batch.events).toMatchObject([
    { kind: "generation-stopped", messageId: Number(id) },
  ]);
});
