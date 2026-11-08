import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { GrammyError } from "grammy";
import { expect, it, vi } from "vitest";
import { openStore } from "../../src/store/index.ts";
import { TransientManager } from "../../src/channels/transient.ts";
import { PendingManager } from "../../src/core/pending.ts";
import type { RuntimeContext } from "../../src/core/context.ts";
import type { TelegramChannel } from "../../src/channels/telegram.ts";

function seed(
  store: ReturnType<typeof openStore>,
  root: string,
  now: Date,
): void {
  store.putEntity({
    id: "e",
    directory: root,
    botId: "bot",
    createdAt: now.toISOString(),
  });
  store.createTurn({
    id: "turn",
    entityId: "e",
    source: "telegram",
    createdAt: now.toISOString(),
  });
  store.transitionTurn("turn", "queued", "active", {
    externalTurnId: "remote",
  });
}
async function setup() {
  const root = await mkdtemp(join(tmpdir(), "thc-pending-retry-"));
  const store = openStore(join(root, "db"));
  const now = new Date("2026-10-31T00:00:00Z");
  seed(store, root, now);
  const rejection = new GrammyError(
    "rate limit",
    {
      ok: false,
      error_code: 429,
      description: "retry",
      parameters: { retry_after: 2 },
    },
    "sendRichMessage",
    {},
  );
  const send = vi
    .fn()
    .mockRejectedValueOnce(rejection)
    .mockResolvedValueOnce({ message_id: 10 })
    .mockRejectedValueOnce(new Error("network uncertain"));
  const channel = {
    bot: { api: { sendRichMessage: send } },
  } as unknown as TelegramChannel;
  const context = {
    store,
    channel,
    transient: new TransientManager(channel, store, () => now),
    adapter: { capabilities: new Set(["approve"]) },
    config: { approval_timeout: null },
    now: () => now,
    log: vi.fn(),
  } as unknown as RuntimeContext;
  const manager = new PendingManager(context);
  const event = {
    kind: "approval-requested" as const,
    sessionId: "session",
    turnId: "remote",
    cursor: "request",
    requestId: "1",
    data: { command: "safe" },
  };
  return { root, store, now, manager, event, send };
}
it("retries definitively rejected cards after the retry deadline but not ambiguous sends", async () => {
  const { root, store, now, manager, event, send } = await setup();
  try {
    await expect(
      manager.request(event, "turn", "chat", "user", "en"),
    ).rejects.toThrow("rate limit");
    await manager.tick();
    expect(send).toHaveBeenCalledTimes(1);
    now.setTime(now.getTime() + 2100);
    await manager.tick();
    expect(send).toHaveBeenCalledTimes(2);
    await expect(
      manager.request(
        { ...event, requestId: "2" },
        "turn",
        "chat",
        "user",
        "en",
      ),
    ).rejects.toThrow("network uncertain");
    now.setTime(now.getTime() + 60000);
    await manager.tick();
    expect(send).toHaveBeenCalledTimes(3);
  } finally {
    store.close();
    await rm(root, { recursive: true, force: true });
  }
});
