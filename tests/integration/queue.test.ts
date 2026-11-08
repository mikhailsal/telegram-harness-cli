import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, it } from "vitest";
import { openStore } from "../../src/store/index.js";

it("persists the queued input contract across a database reopen", () => {
  const directory = mkdtempSync(join(tmpdir(), "thc-queue-contract-"));
  const path = join(directory, "store.db");
  try {
    const first = openStore(path);
    first.putEntity({
      id: "entity",
      directory,
      botId: "bot",
      createdAt: "2026-01-01T00:00:00Z",
    });
    first.recordInbox({
      updateId: 42,
      entityId: "entity",
      chatId: "chat",
      userId: "user",
      receivedAt: "2026-01-01T00:00:00Z",
      payload: "message",
    });
    first.close();
    const second = openStore(path);
    try {
      expect(second.listQueuedInbox()).toMatchObject([
        {
          updateId: 42,
          chatId: "chat",
          userId: "user",
          payload: "message",
          status: "queued",
        },
      ]);
      expect(
        second.recordInbox({
          updateId: 42,
          entityId: "entity",
          chatId: "chat",
          userId: "user",
          receivedAt: "2026-01-01T00:00:00Z",
          payload: "message",
        }),
      ).toBe(false);
    } finally {
      second.close();
    }
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});
