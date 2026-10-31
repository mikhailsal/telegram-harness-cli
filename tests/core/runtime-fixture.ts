import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach } from "vitest";
import type {
  AdapterEvent,
  HarnessAdapter,
} from "../../src/adapters/contract.js";
import type {
  ChannelEvent,
  TelegramChannel,
} from "../../src/channels/index.js";
import { parseConfig } from "../../src/core/config.js";
import { Store } from "../../src/store/index.js";

export let directory: string;
export const at = "2026-10-31T10:00:00.000Z";
export const event: ChannelEvent = {
  kind: "message",
  updateId: 10,
  chatId: "7",
  userId: "7",
  messageId: 20,
  sentAt: at,
  language: "en",
  text: "hello",
  attachments: [],
};
beforeEach(() => {
  directory = mkdtempSync(join(tmpdir(), "thc-runtime-"));
  mkdirSync(join(directory, ".thc"));
  writeFileSync(join(directory, "token"), "123456:abcdefghijklmnop", {
    mode: 0o600,
  });
});
afterEach(() => {
  rmSync(directory, { recursive: true, force: true });
});
export function config() {
  return parseConfig({
    token: { file: join(directory, "token") },
    allowlist: [7],
    sandbox: "read-only",
    status: "off",
    update_check: false,
    codex: { adapter: "protocol", daemon_autostart: false },
  });
}
export class FakeAdapter implements HarnessAdapter {
  readonly capabilities: HarnessAdapter["capabilities"] = new Set([
    "submit",
    "observe",
    "reconcile",
    "attach",
  ]);
  readonly events: AdapterEvent[] = [];
  private readonly thread: string;
  constructor(thread = "thread") {
    this.thread = thread;
  }
  sessions = 0;
  submitted = 0;
  reconciled = 0;
  closed = false;
  wake: (() => void) | undefined;
  newSession(): Promise<{ id: string }> {
    this.sessions++;
    return Promise.resolve({ id: this.thread });
  }
  submit(input: {
    correlationId: string;
    text: string;
  }): Promise<{ turnId: string }> {
    this.submitted++;
    this.events.push({
      kind: "turn-completed",
      sessionId: "thread",
      turnId: "turn",
      correlationId: input.correlationId,
      cursor: "done",
      text: "visible answer",
    });
    this.wake?.();
    return Promise.resolve({ turnId: "turn" });
  }
  async *observe(): AsyncIterable<AdapterEvent> {
    while (!this.closed) {
      const event = this.events.shift();
      if (event) {
        yield event;
        continue;
      }
      await new Promise<void>((resolve) => {
        this.wake = resolve;
      });
    }
  }
  interrupt(): Promise<void> {
    return Promise.resolve();
  }
  approve(): Promise<void> {
    return Promise.resolve();
  }
  reconcile(): Promise<AdapterEvent[]> {
    this.reconciled++;
    return Promise.resolve([]);
  }
  attach(): Promise<{ command: string; args: string[] }> {
    return Promise.resolve({ command: "true", args: [] });
  }
  close(): Promise<void> {
    this.closed = true;
    this.wake?.();
    return Promise.resolve();
  }
}
export class FakeChannel {
  calls: number[] = [];
  deliveries: string[] = [];
  notices: string[] = [];
  deleted: number[] = [];
  conflicts = 0;
  pollFailures = 0;
  updates: ChannelEvent[] = [];
  menus: string[] = [];
  bot = {
    api: {
      sendMessage: (_chat: string, text: string) => {
        this.notices.push(text);
        return Promise.resolve({ message_id: 71 });
      },
      setMyCommands: (
        _commands: unknown,
        options: { language_code: string },
      ) => {
        this.menus.push(options.language_code);
        return Promise.resolve(true);
      },
      editMessageReplyMarkup: () => Promise.resolve(true),
    },
  };
  async poll(
    offset: number,
    signal?: AbortSignal,
  ): Promise<{ events: ChannelEvent[]; nextOffset: number }> {
    this.calls.push(offset);
    if (this.pollFailures > 0) {
      this.pollFailures--;
      throw new Error("temporary network outage");
    }
    if (this.conflicts > 0) {
      this.conflicts--;
      const error = Object.assign(new Error("conflict"), { error_code: 409 });
      throw error;
    }
    const item = this.updates.shift();
    if (item) return { events: [item], nextOffset: item.updateId + 1 };
    await new Promise<void>((resolve) => {
      if (signal?.aborted) resolve();
      else
        signal?.addEventListener(
          "abort",
          () => {
            resolve();
          },
          { once: true },
        );
    });
    return { events: [], nextOffset: offset };
  }
  send(
    _chat: string,
    text: string,
  ): Promise<{ messageIds: number[]; visibleText: string }> {
    this.notices.push(text);
    return Promise.resolve({ messageIds: [50], visibleText: text });
  }
  delete(_chat: string, id: number): Promise<void> {
    this.deleted.push(id);
    return Promise.resolve();
  }
  deliverChunk(
    _chat: string,
    chunk: { text: string },
  ): Promise<{ messageIds: number[]; visibleText: string }> {
    this.deliveries.push(chunk.text);
    return Promise.resolve({
      messageIds: [100 + this.deliveries.length],
      visibleText: chunk.text,
    });
  }
}
export async function waitFor(
  check: () => boolean,
  timeout = 5000,
): Promise<void> {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    if (check()) return;
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
  throw new Error("Timed out waiting for runtime condition");
}
export function inspect<T>(read: (store: Store) => T): T | undefined {
  try {
    const db = new Store(join(directory, ".thc", "store.db"), true);
    try {
      return read(db);
    } finally {
      db.close();
    }
  } catch {
    return;
  }
}
export function channel(fake: FakeChannel): TelegramChannel {
  return fake as unknown as TelegramChannel;
}
