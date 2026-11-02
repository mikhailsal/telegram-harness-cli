import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, it, vi } from "vitest";
import { Supervisor } from "../../src/core/supervisor.js";
import type { RuntimeContext } from "../../src/core/context.js";
import type { TurnEngine } from "../../src/core/engine.js";
import { openStore } from "../../src/store/index.js";

it("commits the new-command marker atomically with session activation", () => {
  const directory = mkdtempSync(join(tmpdir(), "thc-rotation-marker-"));
  const store = openStore(join(directory, "db"));
  try {
    const now = "2026-11-02T12:00:00.000Z";
    store.putEntity({ id: "e", directory, botId: "bot", createdAt: now });
    const session = {
      id: "s",
      entityId: "e",
      harness: "codex",
      externalId: "thread",
      startedAt: now,
      endedAt: null,
    };
    expect(
      store.activateSession(session, "descriptor", "missing", "", 41),
    ).toBe(false);
    expect(store.getCursor("new-completed/41")).toBeUndefined();
    expect(store.getSession("s")).toBeUndefined();
    expect(
      store.activateSession(session, "descriptor", undefined, "", 41),
    ).toBe(true);
    expect(store.getCursor("new-completed/41")).toBe("s");
    expect(store.getCursor("active-session")).toBe("s");
  } finally {
    store.close();
    rmSync(directory, { recursive: true, force: true });
  }
});

it("does not create a second session when a completed command replays", async () => {
  const directory = mkdtempSync(join(tmpdir(), "thc-rotation-replay-"));
  const store = openStore(join(directory, "db"));
  try {
    const now = "2026-11-02T12:00:00.000Z";
    store.putEntity({ id: "e", directory, botId: "bot", createdAt: now });
    store.setCursor("new-completed/41", "existing");
    const factory = vi.fn();
    const ctx = { store } as unknown as RuntimeContext;
    const supervisor = new Supervisor(ctx, {} as TurnEngine, {
      factory,
      freshFactory: factory,
      startDaemon: vi.fn(),
    });
    await supervisor.rotate(41);
    expect(factory).not.toHaveBeenCalled();
  } finally {
    store.close();
    rmSync(directory, { recursive: true, force: true });
  }
});
