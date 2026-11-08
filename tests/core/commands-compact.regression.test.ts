import { expect, it, vi } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { CompactRejectedError } from "../../src/adapters/contract.js";
import { catalog, type ChannelEvent } from "../../src/channels/index.js";
import { Commands } from "../../src/core/commands.js";
import type { RuntimeContext } from "../../src/core/context.js";
import { openStore } from "../../src/store/index.js";

const input: Extract<ChannelEvent, { kind: "command" }> = {
  kind: "command",
  command: "compact",
  arguments: "",
  updateId: 42,
  chatId: "1",
  userId: "1",
  messageId: 5,
  sentAt: "2026-11-02T00:00:00Z",
  language: "en",
};
function setup(compact: () => Promise<void>) {
  const cursors = new Map<string, string>();
  const send = vi.fn().mockResolvedValue(1);
  const context = {
    store: {
      listTurns: () => [],
      getCursor: (key: string) => cursors.get(key),
      setCursor: (key: string, value: string) => {
        cursors.set(key, value);
      },
    },
    adapterReady: true,
    adapter: { compact, capabilities: new Set(["compact"]) },
    transient: { send },
    now: () => new Date("2026-11-02T00:00:00Z"),
    log: vi.fn(),
  } as unknown as RuntimeContext;
  return {
    service: new Commands(context, { finish: () => false }),
    send,
    cursors,
  };
}
it("records intent before a successful compact and never repeats the same update", async () => {
  const compact = vi.fn().mockResolvedValue(undefined);
  const f = setup(compact);
  compact.mockImplementation(() => {
    expect(f.cursors.get("compact-intent/42")).toBe("1");
    return Promise.resolve();
  });
  await f.service.run(input);
  await f.service.run(input);
  expect(compact).toHaveBeenCalledOnce();
  expect(f.send).not.toHaveBeenCalled();
});
it.each([
  [new CompactRejectedError(), catalog.en.compactRejected],
  [new Error("socket disconnected"), catalog.en.compactUncertain],
])(
  "reports compact failure without replaying an ambiguous side effect",
  async (error, message) => {
    const compact = vi.fn().mockRejectedValue(error);
    const f = setup(compact);
    await f.service.run(input);
    await f.service.run(input);
    expect(compact).toHaveBeenCalledOnce();
    expect(f.send).toHaveBeenCalledOnce();
    expect(f.send).toHaveBeenCalledWith(expect.anything(), message);
  },
);
it("does not reissue an ambiguous compact after reopening the durable store", async () => {
  const directory = mkdtempSync(join(tmpdir(), "thc-compact-replay-"));
  const path = join(directory, "db");
  const compact = vi.fn().mockRejectedValue(new Error("connection lost"));
  const send = vi.fn().mockResolvedValue(1);
  let store = openStore(path);
  const service = () =>
    new Commands(
      {
        store,
        adapterReady: true,
        adapter: { compact, capabilities: new Set(["compact"]) },
        transient: { send },
        now: () => new Date("2026-11-02T00:00:00Z"),
        log: vi.fn(),
      } as unknown as RuntimeContext,
      { finish: () => false },
    );
  try {
    await service().run(input);
    store.close();
    store = openStore(path);
    await service().run(input);
    expect(compact).toHaveBeenCalledOnce();
    expect(send).toHaveBeenCalledOnce();
  } finally {
    store.close();
    rmSync(directory, { recursive: true, force: true });
  }
});
