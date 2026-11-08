import { expect, it, vi } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { ChannelEvent } from "../../src/channels/index.js";
import { parseConfig } from "../../src/core/config.js";
import { Commands } from "../../src/core/commands.js";
import type { RuntimeContext } from "../../src/core/context.js";
import { openStore, type Store } from "../../src/store/index.js";
import { normalizeEvent } from "../../src/core/queue.js";

const base = { token: { env: "TOKEN" }, allowlist: [1], sandbox: "read-only" };
it("defaults the configured format to rich and accepts an explicit regular setting", () => {
  expect(parseConfig(base).format).toBe("rich");
  expect(parseConfig({ ...base, format: "regular" }).format).toBe("regular");
  expect(() => parseConfig({ ...base, format: "bogus" })).toThrow();
});
function fixture(store: Store = openStore(":memory:")) {
  const sent = vi.fn().mockResolvedValue({ message_id: 10 });
  const edit = vi.fn().mockResolvedValue(true);
  const answer = vi.fn().mockResolvedValue(undefined);
  const transient = {
    send: vi
      .fn<(intent: unknown, text: string) => Promise<number>>()
      .mockResolvedValue(1),
    create: vi.fn(
      async (
        intent: {
          id: string;
          kind: "command";
          chatId: string;
          createdAt: string;
          expiresAt: string | null;
        },
        send: () => Promise<number>,
      ) => {
        store.putTransient(intent);
        const id = await send();
        store.setTransientMessage(intent.id, id, intent.createdAt);
        return id;
      },
    ),
  };
  const ctx = {
    store,
    config: parseConfig(base),
    now: () => new Date("2026-11-02T00:00:00Z"),
    transient,
    log: vi.fn(),
    channel: {
      answerCallback: answer,
      bot: { api: { sendMessage: sent, editMessageText: edit } },
    },
  } as unknown as RuntimeContext;
  const commands = new Commands(ctx, { finish: () => false });
  return { store, sent, edit, answer, commands, transient };
}
const event: Extract<ChannelEvent, { kind: "command" }> = {
  kind: "command",
  command: "format",
  arguments: "",
  updateId: 1,
  messageId: 5,
  chatId: "1",
  userId: "1",
  sentAt: "2026-11-02T00:00:00Z",
  language: "ru",
};
it.each([
  ["en", "partial text quotes"],
  ["ru", "цитаты фрагментов"],
] as const)(
  "explains format and quote limitations in %s help",
  async (language, limitation) => {
    const { store, commands, transient } = fixture();
    try {
      await commands.run({ ...event, command: "help", language });
      expect(transient.send).toHaveBeenCalledOnce();
      const text = transient.send.mock.calls[0]?.[1] ?? "";
      expect(text).toContain("/format");
      expect(text).toContain(limitation);
    } finally {
      store.close();
    }
  },
);
it("updates a persistent per-chat format through the functional chooser", async () => {
  const { store, sent, edit, answer, commands, transient } = fixture();
  try {
    expect(normalizeEvent(event)).toMatchObject({
      kind: "command",
      command: "format",
    });
    await commands.run(event);
    expect(sent).toHaveBeenCalledOnce();
    expect(JSON.stringify(sent.mock.calls[0])).toContain("format:regular");
    expect(JSON.stringify(sent.mock.calls[0])).toContain("цитат");
    const decision: Extract<ChannelEvent, { kind: "decision" }> = {
      kind: "decision",
      updateId: 2,
      chatId: "1",
      userId: "1",
      messageId: 10,
      callbackId: "cb",
      sentAt: event.sentAt,
      language: "ru",
      data: "format:regular",
    };
    expect(await commands.decision(decision)).toBe(true);
    expect(store.getCursor("message-format/1")).toBe("regular");
    expect(answer).toHaveBeenCalledOnce();
    expect(edit).toHaveBeenCalledOnce();
    expect(edit).toHaveBeenCalledWith(
      "1",
      10,
      expect.stringContaining("Обычный"),
      { reply_markup: { inline_keyboard: [] } },
    );
    await commands.run({ ...event, updateId: 3, arguments: "invalid" });
    expect(store.getCursor("message-format/1")).toBe("regular");
    await commands.run({ ...event, updateId: 4, arguments: "rich" });
    expect(store.getCursor("message-format/1")).toBe("rich");
    expect(transient.send).toHaveBeenCalledWith(
      expect.anything(),
      expect.stringContaining("Rich"),
    );
    await commands.run({
      ...event,
      chatId: "2",
      updateId: 5,
      arguments: "regular",
    });
    expect(store.getCursor("message-format/2")).toBe("regular");
    expect(store.getCursor("message-format/1")).toBe("rich");
  } finally {
    store.close();
  }
});
it("keeps an explicit format across a store restart", async () => {
  const directory = mkdtempSync(join(tmpdir(), "thc-format-setting-"));
  const path = join(directory, "db");
  let store = openStore(path);
  try {
    const { commands } = fixture(store);
    await commands.run({ ...event, arguments: "regular" });
    store.close();
    store = openStore(path);
    expect(store.getCursor("message-format/1")).toBe("regular");
  } finally {
    store.close();
    rmSync(directory, { recursive: true, force: true });
  }
});
it("retains a mode confirmation after selecting a button and on a direct argument", async () => {
  const { store, sent, edit, answer, commands, transient } = fixture();
  const mode = { ...event, command: "mode" };
  try {
    await commands.run(mode);
    expect(sent).toHaveBeenCalledOnce();
    const decision: Extract<ChannelEvent, { kind: "decision" }> = {
      kind: "decision",
      updateId: 2,
      chatId: "1",
      userId: "1",
      messageId: 10,
      callbackId: "mode-cb",
      sentAt: event.sentAt,
      language: "ru",
      data: "mode:queue",
    };
    expect(await commands.decision(decision)).toBe(true);
    expect(store.getCursor("followup-mode")).toBe("queue");
    expect(answer).toHaveBeenCalledOnce();
    expect(store.listTransients()).toHaveLength(1);
    expect(edit).toHaveBeenCalledWith("1", 10, expect.any(String), {
      reply_markup: { inline_keyboard: [] },
    });
    await commands.run({ ...mode, updateId: 3, arguments: "queue" });
    const text = transient.send.mock.calls[0]?.[1];
    expect(text).toBe(edit.mock.calls[0]?.[2]);
    await commands.run({ ...mode, updateId: 4, arguments: "bad" });
    expect(store.getCursor("followup-mode")).toBe("queue");
    expect(sent).toHaveBeenCalledTimes(2);
  } finally {
    store.close();
  }
});
