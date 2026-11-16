import { expect, it, vi } from "vitest";
import type { AdapterEvent } from "../../src/adapters/contract.js";
import { Commands } from "../../src/core/commands.js";
import { AdapterEvents } from "../../src/core/events.js";
import {
  later,
  settingsHeld,
  settingsIdle,
} from "../../src/core/harness-settings-runner.js";
import { applySetting } from "../../src/core/settings-audit.js";
import {
  command,
  decision,
  settingsFixture,
} from "./harness-settings-fixture.js";

type Fixture = ReturnType<typeof settingsFixture>;
const commands = (f: Fixture) => new Commands(f.ctx, { finish: () => false });
async function run(f: Fixture, event: ReturnType<typeof command>) {
  await commands(f).run(event);
  await settingsIdle(f.ctx);
}
async function tap(f: Fixture, data: string, messageId: number, updateId = 50) {
  await commands(f).decision(decision(data, messageId, updateId));
  await settingsIdle(f.ctx);
}
const desired = (f: Fixture) =>
  JSON.parse(
    f.store.getCursor("harness-settings/desired") ?? "null",
  ) as unknown;
const audit = { updateId: 9, chatId: "1", userId: "1", sentAt: "" };
const notFound: AdapterEvent = {
  kind: "turn-error",
  sessionId: "thread",
  cursor: "turn-error/turn-7",
  turnId: "turn-7",
  data: { code: "modelNotFound" },
};
async function notify(f: Fixture) {
  await new AdapterEvents(f.ctx, { finish: () => false }).apply(notFound);
  await settingsIdle(f.ctx);
}

it("refuses an effort from a card shown for a model that is no longer current", async () => {
  const f = settingsFixture();
  await run(f, command("effort"));
  await run(f, command("model", "beta", 2));
  await tap(f, "effort:2", 100);
  expect(desired(f)).toEqual({ model: "beta", effort: "minimal" });
  const edited = String(f.editMessageText.mock.calls[0]?.[2] as unknown);
  expect(edited).toMatch(/^The setting changed meanwhile/u);
  expect(edited).toContain("Reasoning effort for beta");
});

it("turns the recover button stale once the owner changes the model by hand", async () => {
  const f = settingsFixture({ running: { model: "retired" } });
  f.store.setCursor("chat-id", "1");
  applySetting(f.ctx, audit, "harness", { model: "retired" });
  await notify(f);
  expect(settingsHeld(f.ctx)).toBe(true);
  await run(f, command("model", "beta", 2));
  await tap(f, "recover", 100);
  expect(f.answerCallback).toHaveBeenLastCalledWith(
    "cb-50",
    "Already resolved",
  );
  expect(desired(f)).toEqual({ model: "beta", effort: "minimal" });
});

it("holds the model the failing turn was sent with, not a newer choice", async () => {
  const f = settingsFixture();
  f.store.setCursor("chat-id", "1");
  f.store.setCursor("harness-settings/sent", JSON.stringify({ model: "old" }));
  applySetting(f.ctx, audit, "harness", { model: "beta" });
  await notify(f);
  expect(f.store.getCursor("settings-blocked")).toBe("old");
  expect(settingsHeld(f.ctx)).toBe(false);
  expect(f.texts()[0]).toContain("old is not available");
});

it("does not hold the queue when the notice cannot reach the owner", async () => {
  const f = settingsFixture();
  applySetting(f.ctx, audit, "harness", { model: "retired" });
  await notify(f);
  expect(settingsHeld(f.ctx)).toBe(false);
  f.store.setCursor("chat-id", "1");
  f.sendMessage.mockRejectedValueOnce(new Error("403"));
  await new AdapterEvents(f.ctx, { finish: () => false }).apply({
    ...notFound,
    turnId: "turn-8",
  });
  await settingsIdle(f.ctx);
  expect(settingsHeld(f.ctx)).toBe(false);
  expect(f.log).toHaveBeenCalledWith("settings.commit-failed");
});

it("logs a failing pump after a commit instead of rejecting unhandled", async () => {
  const f = settingsFixture();
  const commit = vi.fn().mockResolvedValue(undefined);
  later(
    f.ctx,
    async (action) => {
      await action();
      throw new Error("pump");
    },
    () => Promise.resolve(1),
    commit,
  );
  await settingsIdle(f.ctx);
  expect(commit).toHaveBeenCalledWith({ ok: true, value: 1 });
  expect(f.log).toHaveBeenCalledWith("settings.pump-failed");
});

it("drops a recover tap when the setting changes while the list is read", async () => {
  const f = settingsFixture({ running: { model: "retired" } });
  f.store.setCursor("chat-id", "1");
  applySetting(f.ctx, audit, "harness", { model: "retired" });
  await notify(f);
  let release: (() => void) | undefined;
  f.listModels.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        release = () => {
          resolve([]);
        };
      }),
  );
  await commands(f).decision(decision("recover", 100));
  await new Promise((resolve) => setImmediate(resolve));
  applySetting(f.ctx, { ...audit, updateId: 10 }, "harness", { model: "beta" });
  release?.();
  await settingsIdle(f.ctx);
  expect(f.texts().at(-1)).toBe("Already resolved");
  expect(desired(f)).toEqual({ model: "beta" });
});

it("treats unreadable recover and sent cursors as absent", async () => {
  const f = settingsFixture();
  f.store.setCursor("settings-recover/1/100", "1");
  f.store.setCursor("settings-recover/1/101", "{");
  await tap(f, "recover", 100);
  await tap(f, "recover", 101, 51);
  expect(
    f.answerCallback.mock.calls.map((call) => String(call[1] as unknown)),
  ).toEqual(["Already resolved", "Already resolved"]);
  f.store.setCursor("chat-id", "1");
  f.store.setCursor("harness-settings/sent", "{");
  applySetting(f.ctx, audit, "harness", { model: "beta" });
  await notify(f);
  expect(f.store.getCursor("settings-blocked")).toBeUndefined();
});
