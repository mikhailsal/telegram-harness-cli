import { expect, it } from "vitest";
import type { AdapterEvent } from "../../src/adapters/contract.js";
import { Commands } from "../../src/core/commands.js";
import { AdapterEvents } from "../../src/core/events.js";
import { settingsIdle } from "../../src/core/harness-settings-runner.js";
import { applySetting } from "../../src/core/settings-audit.js";
import {
  alpha,
  beta,
  catalogModels,
  command,
  decision,
  settingsFixture,
  telegramTurn,
} from "./harness-settings-fixture.js";

type Fixture = ReturnType<typeof settingsFixture>;
const commands = (f: Fixture) => new Commands(f.ctx, { finish: () => false });
async function run(f: Fixture, event: ReturnType<typeof command>) {
  await commands(f).run(event);
  await settingsIdle(f.ctx);
}
async function tap(f: Fixture, data: string, messageId: number, updateId = 50) {
  const handled = await commands(f).decision(
    decision(data, messageId, updateId),
  );
  await settingsIdle(f.ctx);
  return handled;
}
const desired = (f: Fixture) =>
  JSON.parse(
    f.store.getCursor("harness-settings/desired") ?? "null",
  ) as unknown;
const edits = (f: Fixture) =>
  f.editMessageText.mock.calls.map((call) => String(call[2] as unknown));

it("saves the model on the first tap, then offers its efforts and confirms the second tap", async () => {
  const f = settingsFixture();
  await run(f, command("model"));
  expect(await tap(f, "model:1", 100)).toBe(true);
  expect(f.answerCallback).toHaveBeenCalledWith("cb-50", undefined);
  expect(desired(f)).toEqual({ model: "beta", effort: "minimal" });
  expect(edits(f)[0]).toContain(
    "Model saved: beta · effort minimal. Applies to the next turn, queued messages included.",
  );
  expect(edits(f)[0]).toContain("beta does not support effort high");
  const markup = f.editMessageText.mock.calls[0]?.[3] as {
    reply_markup: { inline_keyboard: { text: string }[][] };
  };
  expect(markup.reply_markup.inline_keyboard.flat()[0]?.text).toBe(
    "✓ minimal (default)",
  );
  await tap(f, "effort:1", 100, 51);
  expect(desired(f)).toEqual({ model: "beta", effort: "low" });
  expect(edits(f)[1]).toContain("✓ Effort low for beta");
  expect(f.store.getCursor("settings-card/1/100")).toBe("");
});

it("answers stale for a removed card, a wrong kind or an unknown index", async () => {
  const f = settingsFixture();
  expect(await tap(f, "model:0", 100)).toBe(true);
  await run(f, command("model"));
  await tap(f, "effort:0", 100, 51);
  await tap(f, "model:7", 100, 52);
  expect(
    f.answerCallback.mock.calls.map((call) => String(call[1] as unknown)),
  ).toEqual(["Already resolved", "Already resolved", "Already resolved"]);
  expect(await commands(f).decision(decision("other:1", 100))).toBe(false);
  expect(desired(f)).toBeNull();
});

it("answers the callback with the reason when Codex is not connected", async () => {
  const f = settingsFixture();
  await run(f, command("model"));
  Object.assign(f.ctx, { adapterReady: false });
  await tap(f, "model:0", 100);
  expect(String(f.answerCallback.mock.calls[0]?.[1])).toContain(
    "not connected yet",
  );
  expect(f.listModels).toHaveBeenCalledTimes(1);
});

it("refreshes the card when the pick is gone or the setting changed meanwhile", async () => {
  const f = settingsFixture();
  await run(f, command("model"));
  f.listModels.mockResolvedValueOnce([alpha()]);
  await tap(f, "model:1", 100);
  expect(edits(f)[0]).toMatch(/^beta is no longer offered\. Nothing changed/u);
  expect(desired(f)).toBeNull();
  await run(f, command("effort", "", 2));
  let release: (() => void) | undefined;
  f.listModels.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        release = () => {
          resolve(catalogModels());
        };
      }),
  );
  const pending = commands(f).decision(decision("effort:0", 101, 51));
  await pending;
  await new Promise((resolve) => setImmediate(resolve));
  applySetting(
    f.ctx,
    { updateId: 9, chatId: "1", userId: "1", sentAt: "" },
    "harness",
    { model: "alpha" },
  );
  release?.();
  await settingsIdle(f.ctx);
  expect(edits(f)[1]).toMatch(/^The setting changed meanwhile/u);
  expect(edits(f)[1]).toContain("Reasoning effort for alpha");
  expect(desired(f)).toEqual({ model: "alpha" });
});

it("rejects an effort the model no longer supports and reports read failures", async () => {
  const f = settingsFixture();
  await run(f, command("effort"));
  f.listModels.mockResolvedValueOnce([]);
  await tap(f, "effort:0", 100);
  expect(edits(f)[0]).toContain("low is no longer offered");
  await run(f, command("effort", "", 2));
  f.listModels.mockRejectedValueOnce(new Error("timed out"));
  await tap(f, "effort:0", 101, 51);
  expect(f.texts().at(-1)).toContain("(timeout). Nothing changed.");
  expect(desired(f)).toBeNull();
});

it("sends the confirmation as a new message when the card edit fails", async () => {
  const f = settingsFixture();
  await run(f, command("model"));
  f.editMessageText.mockRejectedValueOnce(new Error("400"));
  await tap(f, "model:1", 100);
  expect(f.log).toHaveBeenCalledWith("settings.card-edit-failed");
  expect(f.texts()[1]).toContain("Model saved: beta");
  expect(
    JSON.parse(f.store.getCursor("settings-card/1/101") ?? "{}"),
  ).toMatchObject({ kind: "effort", model: "beta" });
  f.editMessageText.mockRejectedValueOnce(new Error("400"));
  await tap(f, "effort:0", 101, 51);
  expect(f.texts()[2]).toContain("✓ Effort minimal for beta");
});

it("confirms a model without efforts in one tap", async () => {
  const f = settingsFixture({
    models: [{ ...alpha(), supportedReasoningEfforts: [] }, beta()],
  });
  await run(f, command("model"));
  await tap(f, "model:0", 100);
  expect(desired(f)).toEqual({ model: "alpha" });
  expect(edits(f)[0]).toContain("✓ Model alpha");
});

const notFound = (turnId = "turn-7"): AdapterEvent => ({
  kind: "turn-error",
  sessionId: "thread",
  cursor: `turn-error/${turnId}`,
  turnId,
  data: { code: "modelNotFound" },
});
async function notify(f: Fixture, event: AdapterEvent) {
  if (event.turnId) telegramTurn(f.store, event.turnId);
  await new AdapterEvents(f.ctx, { finish: () => false }).apply(event);
  await settingsIdle(f.ctx);
}

it("notifies once per turn, holds the queue and repairs on one tap", async () => {
  const f = settingsFixture({ running: { model: "retired" } });
  f.store.setCursor("chat-id", "1");
  applySetting(
    f.ctx,
    { updateId: 1, chatId: "1", userId: "1", sentAt: "" },
    "harness",
    { model: "retired" },
  );
  await notify(f, notFound());
  await notify(f, notFound());
  await notify(f, { ...notFound("turn-8"), data: { code: "other" } });
  expect(f.texts()).toHaveLength(1);
  expect(f.texts()[0]).toContain("retired is not available");
  expect(f.texts()[0]).toContain("Your last message was not processed");
  expect(f.texts()[0]).toContain("Queued messages wait until you switch");
  expect(f.lastMarkup()).toEqual([
    [{ text: "Switch to alpha", callback_data: "recover" }],
  ]);
  expect(f.store.getCursor("settings-blocked")).toBe("retired");
  expect(f.log).toHaveBeenCalledWith("settings.model-unavailable", {
    model: "retired",
  });
  await tap(f, "recover", 100);
  expect(desired(f)).toEqual({ model: "alpha", effort: "medium" });
  expect(f.store.getCursor("settings-blocked")).toBe("");
  expect(edits(f)[0]).toContain("✓ Model alpha · effort medium");
  await tap(f, "recover", 100, 51);
  expect(f.answerCallback).toHaveBeenLastCalledWith(
    "cb-51",
    "Already resolved",
  );
});

it("names /model when no default can be offered", async () => {
  const f = settingsFixture();
  f.store.setCursor("chat-id", "1");
  f.store.setCursor("language", "ru");
  f.listModels.mockRejectedValueOnce(new Error("down"));
  await notify(f, notFound());
  expect(f.texts()[0]).toContain("Выбери другую модель через /model");
  expect(f.lastMarkup()).toBeUndefined();
  expect(f.store.getCursor("settings-blocked")).toBeUndefined();
  const silent = settingsFixture();
  await notify(silent, notFound());
  expect(silent.texts()).toHaveLength(0);
});

it("reports a failed repair read and an empty list without changing anything", async () => {
  const f = settingsFixture();
  f.store.setCursor("chat-id", "1");
  await notify(f, notFound());
  f.listModels.mockRejectedValueOnce(new Error("timed out"));
  await tap(f, "recover", 100);
  f.listModels.mockResolvedValueOnce([]);
  await tap(f, "recover", 100, 51);
  expect(f.texts().slice(1)).toEqual([
    expect.stringContaining("(timeout)"),
    expect.stringContaining("no selectable models"),
  ]);
  expect(desired(f)).toBeNull();
  Object.assign(f.ctx, { adapterReady: false });
  await tap(f, "recover", 100, 52);
  expect(String(f.answerCallback.mock.lastCall?.[1])).toContain(
    "not connected",
  );
});

it("shows what runs now, what Telegram sets next and why they differ", async () => {
  const f = settingsFixture();
  await run(f, command("status"));
  expect(f.texts()[0]).toContain("Running: alpha · effort high");
  expect(f.texts()[0]).toContain("Next turns: no Telegram override");
  applySetting(
    f.ctx,
    { updateId: 2, chatId: "1", userId: "1", sentAt: "" },
    "harness",
    { model: "beta", effort: "low" },
  );
  await run(f, command("status", "", 3));
  expect(f.texts()[1]).toContain(
    "Next turns: beta · effort low (Telegram override)",
  );
  expect(f.texts()[1]).toContain("pending: applies from the next turn");
  f.store.setCursor(
    "harness-settings/sent",
    JSON.stringify({ model: "beta", effort: "low" }),
  );
  await run(f, command("status", "", 4));
  expect(f.texts()[2]).toContain("changed in the TUI?");
  f.currentSettings.mockRejectedValueOnce(new Error("gone"));
  await run(f, command("status", "", 5));
  expect(f.texts()[3]).toContain("Running: unknown");
  expect(f.texts()[3]).not.toContain("pending");
  Object.assign(f.ctx, { adapterReady: false });
  await run(f, command("status", "", 6));
  expect(f.texts()[4]).toContain("Running: unknown");
  const tmux = settingsFixture({ configure: false });
  await run(tmux, command("status"));
  expect(tmux.texts()[0]).not.toContain("Running");
});
