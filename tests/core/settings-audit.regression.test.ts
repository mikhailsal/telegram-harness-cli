import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, it, vi } from "vitest";
import { openStore } from "../../src/store/index.js";
import { parseConfig } from "../../src/core/config.js";
import type { RuntimeContext } from "../../src/core/context.js";
import {
  applySetting,
  effectiveSettings,
  recordExecution,
} from "../../src/core/settings-audit.js";
import { status } from "../../src/cli/admin.js";

function expectSavedSettings(
  store: ReturnType<typeof openStore>,
  sentAt: string,
): void {
  expect(store.getCursor("followup-mode")).toBe("queue");
  expect(store.getCursor("message-format/1")).toBe("regular");
  expect(JSON.parse(store.getCursor("setting-applied/1") ?? "null")).toEqual({
    updateId: 1,
    chatId: "1",
    userId: "1",
    sentAt,
    appliedAt: "2026-11-02T00:01:00.000Z",
    setting: "mode",
    value: "steer",
  });
}

it("journals each update atomically, ignores replay and restores settings after reopen", () => {
  const root = mkdtempSync(join(tmpdir(), "thc-setting-"));
  const path = join(root, "store.db");
  let store = openStore(path);
  const log = vi.fn();
  const ctx = {
    store,
    log,
    now: () => new Date("2026-11-02T00:01:00Z"),
    config: parseConfig({
      token: { env: "TOKEN" },
      allowlist: [1],
      sandbox: "read-only",
    }),
  } as unknown as RuntimeContext;
  const event = {
    updateId: 1,
    chatId: "1",
    userId: "1",
    sentAt: "2026-11-02T00:00:00Z",
    text: "private prompt",
  };
  try {
    expect(applySetting(ctx, event, "mode", "steer")).toBe(true);
    expect(applySetting(ctx, { ...event, updateId: 2 }, "mode", "queue")).toBe(
      true,
    );
    expect(applySetting(ctx, event, "mode", "steer")).toBe(false);
    expect(
      applySetting(ctx, { ...event, updateId: 3 }, "format", "regular"),
    ).toBe(true);
    expect(effectiveSettings(ctx, "1")).toEqual({
      mode: "queue",
      format: "regular",
    });
    expect(store.getCursor("setting-applied/1")).not.toContain(
      "private prompt",
    );
    expect(log).toHaveBeenCalledTimes(3);
    store.close();
    store = openStore(path);
    expectSavedSettings(store, event.sentAt);
  } finally {
    store.close();
    rmSync(root, { recursive: true, force: true });
  }
});

it("preserves the effective mode and format for each submission or steering attempt", () => {
  const store = openStore(":memory:");
  const now = () => new Date("2026-11-02T00:00:00Z");
  const log = vi.fn();
  const config = parseConfig({
    token: { env: "TOKEN" },
    allowlist: [1],
    sandbox: "read-only",
  });
  const ctx = {
    store,
    log,
    now,
    config,
    sessionId: "s",
  } as unknown as RuntimeContext;
  try {
    store.putEntity({
      id: "e",
      botId: "b",
      directory: "/tmp/audit",
      createdAt: now().toISOString(),
    });
    store.createTurn({
      id: "t",
      entityId: "e",
      source: "telegram",
      createdAt: now().toISOString(),
    });
    const turn = store.getTurn("t");
    if (!turn) throw new Error("Missing test turn");
    store.setCursor("message-format/1", "regular");
    store.setCursor("followup-mode", "queue");
    recordExecution(ctx, turn, "submit");
    store.setCursor("message-format/1", "rich");
    store.setCursor("followup-mode", "steer");
    recordExecution(ctx, turn, "submit");
    recordExecution(ctx, turn, "steer");
    expect(
      JSON.parse(store.getCursor("turn-execution/t/submit") ?? "null"),
    ).toMatchObject({ mode: "queue", format: "regular", action: "submit" });
    expect(
      JSON.parse(store.getCursor("turn-execution/t/steer") ?? "null"),
    ).toMatchObject({ mode: "steer", format: "regular", action: "steer" });
    expect(log).toHaveBeenCalledTimes(2);
  } finally {
    store.close();
  }
});

it("CLI status reads the effective followup-mode cursor", async () => {
  const root = mkdtempSync(join(tmpdir(), "thc-cli-setting-"));
  const { mkdirSync } = await import("node:fs");
  mkdirSync(join(root, ".thc"));
  const store = openStore(join(root, ".thc", "store.db"));
  store.setCursor("mode", "queue");
  store.setCursor("followup-mode", "steer");
  store.close();
  try {
    expect((await status(root)).mode).toBe("steer");
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

it("CLI status uses configured mode until a Telegram command overrides it", async () => {
  const root = mkdtempSync(join(tmpdir(), "thc-cli-mode-"));
  const { mkdirSync, writeFileSync } = await import("node:fs");
  mkdirSync(join(root, ".thc"));
  writeFileSync(
    join(root, ".thc", "config.json"),
    JSON.stringify({
      token: { env: "TOKEN" },
      allowlist: [1],
      sandbox: "read-only",
      mode: "steer",
    }),
  );
  const store = openStore(join(root, ".thc", "store.db"));
  try {
    expect((await status(root)).mode).toBe("steer");
    store.setCursor("followup-mode", "queue");
    expect((await status(root)).mode).toBe("queue");
  } finally {
    store.close();
    rmSync(root, { recursive: true, force: true });
  }
});

it("rolls back the journal when the setting cannot be saved", () => {
  const store = openStore(":memory:");
  const log = vi.fn();
  const ctx = {
    store,
    log,
    now: () => new Date(),
  } as unknown as RuntimeContext;
  const event = {
    updateId: 42,
    chatId: "1",
    userId: "1",
    sentAt: "2026-11-02T00:00:00Z",
  };
  try {
    const save = vi.spyOn(store, "setCursor").mockImplementation(() => {
      throw new Error("save failed");
    });
    expect(() => applySetting(ctx, event, "mode", "steer")).toThrow(
      "save failed",
    );
    expect(store.getCursor("setting-applied/42")).toBeUndefined();
    expect(log).not.toHaveBeenCalled();
    save.mockRestore();
    expect(applySetting(ctx, event, "mode", "steer")).toBe(true);
  } finally {
    store.close();
  }
});
