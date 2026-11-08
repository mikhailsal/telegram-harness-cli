import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, expect, it, vi } from "vitest";
import { openStore } from "../../src/store/index.js";
import type { Store, Turn } from "../../src/store/index.js";
import { parseConfig } from "../../src/core/config.js";
import type { RuntimeContext } from "../../src/core/context.js";
import { messageFormat } from "../../src/core/format.js";
import { TurnStatuses } from "../../src/core/status.js";

const roots: string[] = [];
const at = "2026-11-02T12:00:00.000Z";
afterEach(() => {
  for (const root of roots.splice(0))
    rmSync(root, { recursive: true, force: true });
});
function fixture() {
  const root = mkdtempSync(join(tmpdir(), "thc-format-"));
  roots.push(root);
  const path = join(root, "store.db");
  const store = openStore(path);
  store.putEntity({ id: "e", directory: root, botId: "b", createdAt: at });
  store.putSession({
    id: "s",
    entityId: "e",
    harness: "codex",
    externalId: "thread",
    startedAt: at,
    endedAt: null,
  });
  return { store, path };
}
function context(store: Store, adapterReady = true): RuntimeContext {
  return {
    store,
    config: parseConfig({
      token: { env: "TOKEN" },
      allowlist: [1, 2],
      sandbox: "read-only",
      status: "draft",
    }),
    entityId: "e",
    sessionId: "s",
    adapterReady,
    now: () => new Date(at),
    channel: {},
    transient: { remove: () => Promise.resolve() },
    log: () => undefined,
  } as unknown as RuntimeContext;
}
function turn(
  store: Store,
  id: string,
  source: Turn["source"] = "telegram",
  chat?: string,
): Turn {
  store.createTurn({ id, entityId: "e", source, createdAt: at });
  if (chat)
    store.recordVisibleMessage({
      id: `inbound/${id}`,
      chatId: chat,
      telegramMessageId: Number(id.replace(/\D/g, "")) || 1,
      author: "user",
      sentAt: at,
      text: "task",
      turnId: id,
      possiblyDuplicated: false,
    });
  const result = store.getTurn(id);
  if (!result) throw new Error("missing turn");
  return result;
}
it("snapshots each chat at turn start and preserves active format across restart", () => {
  const { store, path } = fixture();
  let closed = false;
  try {
    store.setCursor("chat-id", "2");
    store.setCursor("message-format/1", "regular");
    const active = turn(store, "turn1", "telegram", "1");
    const queued = turn(store, "turn2", "telegram", "1");
    expect(messageFormat(context(store), active)).toBe("regular");
    store.setCursor("message-format/1", "rich");
    expect(messageFormat(context(store), active)).toBe("regular");
    store.close();
    closed = true;
    const reopened = openStore(path);
    try {
      expect(messageFormat(context(reopened), active)).toBe("regular");
      expect(messageFormat(context(reopened), queued)).toBe("rich");
      expect(
        messageFormat(
          context(reopened),
          turn(reopened, "turn3", "telegram", "2"),
        ),
      ).toBe("rich");
    } finally {
      reopened.close();
    }
  } finally {
    if (!closed) store.close();
  }
});
it("routes terminal turns to the actual selected chat with config default fallback", () => {
  const { store } = fixture();
  try {
    store.setCursor("chat-id", "2");
    store.setCursor("message-format/1", "regular");
    const terminal = turn(store, "terminal", "terminal");
    expect(messageFormat(context(store), terminal)).toBe("rich");
    store.setCursor("message-format/2", "regular");
    expect(
      messageFormat(context(store), turn(store, "terminal2", "terminal")),
    ).toBe("regular");
  } finally {
    store.close();
  }
});
it("does not freeze an offline queued turn before the user changes its format", async () => {
  const { store } = fixture();
  const ctx = context(store, false);
  const statuses = new TurnStatuses(ctx);
  try {
    store.setCursor("chat-id", "1");
    store.setCursor("message-format/1", "regular");
    const queued = turn(store, "turn1", "telegram", "1");
    statuses.refresh();
    store.setCursor("message-format/1", "rich");
    expect(messageFormat(ctx, queued)).toBe("rich");
  } finally {
    await statuses.close();
    store.close();
  }
});

it("keeps the native preview family unchanged when the answer format changes", async () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date(at));
  const { store } = fixture();
  const ctx = context(store, false);
  const draft = vi.fn().mockResolvedValue(undefined);
  const finalizeDraft = vi.fn().mockResolvedValue(999);
  const remove = vi.fn().mockResolvedValue(undefined);
  ctx.now = () => new Date();
  ctx.channel = {
    draft,
    finalizeDraft,
    hasDraft: () => true,
  } as unknown as RuntimeContext["channel"];
  ctx.transient = { remove } as unknown as RuntimeContext["transient"];
  const statuses = new TurnStatuses(ctx);
  try {
    store.setCursor("chat-id", "1");
    store.setCursor("message-format/1", "regular");
    turn(store, "turn1", "telegram", "1");
    statuses.refresh();
    await vi.advanceTimersByTimeAsync(1);
    expect(draft).toHaveBeenCalledWith(
      1,
      expect.any(Number),
      expect.any(String),
    );
    store.setCursor("message-format/1", "rich");
    store.transitionTurn("turn1", "queued", "active");
    const active = store.getTurn("turn1");
    if (!active) throw new Error("missing turn");
    expect(messageFormat(ctx, active)).toBe("rich");
    expect(await statuses.pause(active.id)).toBe(true);
    expect(store.getCursor(`status-move/${active.id}`)).toBeUndefined();
    expect(finalizeDraft).not.toHaveBeenCalled();
    expect(remove).not.toHaveBeenCalled();
  } finally {
    await statuses.close();
    store.close();
    vi.useRealTimers();
  }
});
