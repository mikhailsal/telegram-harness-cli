import { expect, it, vi } from "vitest";
import {
  TransientManager,
  transientIntent,
  type ChannelEvent,
  type TelegramChannel,
} from "../../src/channels/index.js";
import { parseConfig } from "../../src/core/config.js";
import type { RuntimeContext } from "../../src/core/context.js";
import { Delivery } from "../../src/core/delivery.js";
import { TurnEngine } from "../../src/core/engine.js";
import { recordEvent } from "../../src/core/queue.js";
import { openStore } from "../../src/store/index.js";

it("atomically stores a known command as visible user history and a control inbox item", () => {
  const store = openStore(":memory:");
  const now = new Date("2026-11-02T12:00:00Z");
  store.putEntity({
    id: "e",
    directory: "/tmp",
    botId: "bot",
    createdAt: now.toISOString(),
  });
  const ctx = { store, entityId: "e", now: () => now } as RuntimeContext;
  const event: ChannelEvent = {
    kind: "command",
    command: "mode",
    arguments: "queue",
    updateId: 9,
    chatId: "1",
    userId: "1",
    messageId: 9,
    sentAt: now.toISOString(),
    language: "en",
  };
  try {
    expect(recordEvent(ctx, event)).toBe(true);
    expect(recordEvent(ctx, event)).toBe(false);
    expect(store.visibleMessage("1", 9)).toMatchObject({
      author: "user",
      text: "/mode queue",
    });
    expect(JSON.parse(store.getInbox(9)?.payload ?? "null")).toMatchObject({
      kind: "command",
    });
  } finally {
    store.close();
  }
});

function retentionFixture() {
  const store = openStore(":memory:");
  const now = new Date("2026-11-02T12:00:00.000Z");
  store.putEntity({
    id: "e",
    directory: "/tmp",
    botId: "bot",
    createdAt: now.toISOString(),
  });
  const remove = vi.fn().mockResolvedValue(undefined);
  const submit = vi.fn();
  const channel = { delete: remove } as unknown as TelegramChannel;
  const transient = new TransientManager(channel, store, () => now);
  transient.registerExisting(
    transientIntent("command-input/100", "command", "1", now),
    100,
  );
  const ctx = {
    store,
    channel,
    transient,
    now: () => now,
    entityId: "e",
    sessionId: "s",
    adapterReady: false,
    config: parseConfig({
      token: { env: "TOKEN" },
      allowlist: [1],
      sandbox: "read-only",
      status: "off",
    }),
    adapter: { capabilities: new Set(), submit },
    log: vi.fn(),
    persistAdapter: vi.fn(),
  } as unknown as RuntimeContext;
  const engine = new TurnEngine(ctx, new Delivery(ctx));
  return { store, now, remove, transient, submit, engine };
}
function command(now: Date, id: number, name: string, args = ""): ChannelEvent {
  return {
    kind: "command",
    command: name,
    arguments: args,
    updateId: id,
    chatId: "1",
    userId: "1",
    messageId: id,
    sentAt: now.toISOString(),
    language: "en",
  };
}
it("retains command messages in visible history without submitting them and forgets old cleanup records", async () => {
  const { store, now, remove, transient, submit, engine } = retentionFixture();
  try {
    await engine.accept(
      [command(now, 101, "new"), command(now, 102, "stop")],
      103,
    );
    expect(remove).not.toHaveBeenCalled();
    expect(store.listTransients()).toEqual([]);
    expect(store.visibleMessage("1", 101)).toMatchObject({
      author: "user",
      text: "/new",
    });
    expect(store.visibleMessage("1", 102)).toMatchObject({
      author: "user",
      text: "/stop",
    });
    expect(store.listInbox(["consumed"])).toHaveLength(2);
    expect(store.listTurns(["queued", "submitting", "active"])).toEqual([]);
    expect(submit).not.toHaveBeenCalled();
    transient.registerExisting(
      transientIntent("command-input/103", "command", "1", now),
      103,
    );
    await transient.sweep(new Date(now.getTime() + 11 * 60 * 1000));
    expect(remove).not.toHaveBeenCalled();
    expect(store.listTransients()).toEqual([]);
  } finally {
    await engine.close();
    store.close();
  }
});
