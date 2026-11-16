import { expect, it } from "vitest";
import { SettingsUnsupportedError } from "../../src/adapters/contract.js";
import type { HarnessModel } from "../../src/adapters/contract.js";
import { Commands } from "../../src/core/commands.js";
import { settingsIdle } from "../../src/core/harness-settings-runner.js";
import { applySetting } from "../../src/core/settings-audit.js";
import { command, model, settingsFixture } from "./harness-settings-fixture.js";

async function run(
  f: ReturnType<typeof settingsFixture>,
  event: ReturnType<typeof command>,
): Promise<void> {
  await new Commands(f.ctx, { finish: () => false }).run(event);
  await settingsIdle(f.ctx);
}
const desired = (f: ReturnType<typeof settingsFixture>) =>
  f.store.getCursor("harness-settings/desired");

it("replies without any RPC on tmux or before Codex connects", async () => {
  const tmux = settingsFixture({ configure: false });
  await run(tmux, command("model"));
  expect(tmux.texts()[0]).toContain("needs the protocol adapter");
  expect(tmux.texts()[0]).toContain("Nothing changed");
  expect(tmux.listModels).not.toHaveBeenCalled();
  const offline = settingsFixture();
  Object.assign(offline.ctx, { adapterReady: false });
  await run(offline, command("effort", "low"));
  expect(offline.texts()[0]).toContain("not connected yet");
  expect(offline.listModels).not.toHaveBeenCalled();
});

it("shows visible models with descriptions, the current one checked, and the typed forms", async () => {
  const f = settingsFixture();
  await run(f, command("model"));
  const text = f.texts()[0] ?? "";
  expect(text).toContain("Codex model: alpha · effort high");
  expect(text).toContain("• ALPHA (alpha): alpha for tests");
  expect(text).not.toContain("ghost");
  expect(text).toContain("/model default (pin Codex's default)");
  expect(f.lastMarkup()).toEqual([
    [{ text: "✓ ALPHA", callback_data: "model:0" }],
    [{ text: "BETA", callback_data: "model:1" }],
  ]);
  expect(JSON.parse(f.store.getCursor("settings-card/1/100") ?? "{}")).toEqual({
    kind: "model",
    ids: ["alpha", "beta"],
  });
});

it("caps the card at 24 buttons and points to the typed form", async () => {
  const many = Array.from({ length: 30 }, (_, index) =>
    model(`m${String(index)}`),
  );
  const f = settingsFixture({ models: many });
  await run(f, command("model"));
  expect(f.lastMarkup()).toHaveLength(24);
  expect(f.texts()[0]).toContain("Other models: /model <id>");
});

it("switches by id, resets an unsupported effort and says when it applies", async () => {
  const f = settingsFixture();
  await run(f, command("model", "BETA"));
  expect(JSON.parse(desired(f) ?? "{}")).toEqual({
    model: "beta",
    effort: "minimal",
  });
  const text = f.texts()[0] ?? "";
  expect(text).toContain("✓ Model beta · effort minimal");
  expect(text).toContain("including queued messages");
  expect(text).toContain("beta does not support effort high");
  expect(f.log).toHaveBeenCalledWith(
    "setting.applied",
    expect.objectContaining({ setting: "harness" }),
  );
});

it("keeps a supported effort without pinning it", async () => {
  const f = settingsFixture({ running: { model: "beta", effort: "low" } });
  await run(f, command("model", "alpha"));
  expect(JSON.parse(desired(f) ?? "{}")).toEqual({ model: "alpha" });
  expect(f.texts()[0]).toContain("effort low");
});

it("echoes an unknown id and lists the valid ones in monospace", async () => {
  const f = settingsFixture();
  await run(f, command("model", "gpt-9"));
  const text = f.texts()[0] ?? "";
  const entities = f.lastEntities();
  expect(text).toBe('"gpt-9" is not a model here. Choose one of: alpha beta');
  expect(
    entities.map((entity) =>
      text.slice(entity.offset, entity.offset + entity.length),
    ),
  ).toEqual(["alpha", "beta"]);
  expect(entities.every((entity) => entity.type === "code")).toBe(true);
  expect(desired(f)).toBeUndefined();
});

it("pins Codex's default and resets the override without listing models", async () => {
  const f = settingsFixture();
  await run(f, command("model", "default"));
  expect(JSON.parse(desired(f) ?? "{}")).toEqual({
    model: "alpha",
    effort: "medium",
  });
  expect(f.texts()[0]).toContain("Pinned Codex's default model alpha");
  f.store.setCursor("settings-blocked", "alpha");
  f.listModels.mockClear();
  await run(f, command("model", "reset", 2));
  expect(desired(f)).toBe("{}");
  expect(f.store.getCursor("settings-blocked")).toBe("");
  expect(f.listModels).not.toHaveBeenCalled();
  expect(f.texts()[1]).toContain("The thread keeps alpha · high");
});

it("offers the current model's efforts, checking the applied one and labelling the default", async () => {
  const f = settingsFixture();
  await run(f, command("effort"));
  expect(f.texts()[0]).toContain("• medium (default): balanced");
  expect(f.lastMarkup()).toEqual([
    [{ text: "low", callback_data: "effort:0" }],
    [{ text: "medium (default)", callback_data: "effort:1" }],
    [{ text: "✓ high", callback_data: "effort:2" }],
  ]);
  expect(f.texts()[0]).toContain("Type: /effort <level>");
});

it("stores an effort alone when no model is chosen and rejects an unsupported level", async () => {
  const f = settingsFixture();
  await run(f, command("effort", "LOW"));
  expect(JSON.parse(desired(f) ?? "{}")).toEqual({ effort: "low" });
  expect(f.texts()[0]).toContain("✓ Effort low for alpha");
  await run(f, command("effort", "extreme", 2));
  expect(f.texts()[1]).toBe(
    '"extreme" is not supported by alpha. Choose one of: low medium high',
  );
});

it("explains empty model and effort lists", async () => {
  const empty = settingsFixture({ models: [] });
  await run(empty, command("model"));
  expect(empty.texts()[0]).toContain("no selectable models");
  await run(empty, command("effort", "", 2));
  expect(empty.texts()[1]).toContain("no selectable models");
  const flat: HarnessModel = model("flat", {
    isDefault: true,
    supportedReasoningEfforts: [],
  });
  const f = settingsFixture({ models: [flat], running: {} });
  await run(f, command("effort"));
  expect(f.texts()[0]).toContain("flat has no reasoning effort levels");
  await run(f, command("model", "flat", 2));
  expect(JSON.parse(desired(f) ?? "{}")).toEqual({ model: "flat" });
  await run(f, command("model", "default", 3));
  expect(JSON.parse(desired(f) ?? "{}")).toEqual({ model: "flat" });
});

it("reports read failures with their reason and changes nothing", async () => {
  const f = settingsFixture();
  f.listModels.mockRejectedValueOnce(
    Object.assign(new Error("boom"), { code: -32000 }),
  );
  await run(f, command("model", "beta"));
  f.listModels.mockRejectedValueOnce(new Error("Protocol request aborted"));
  await run(f, command("model", "beta", 2));
  f.listModels.mockRejectedValueOnce(new SettingsUnsupportedError());
  await run(f, command("model", "beta", 3));
  f.listModels.mockRejectedValueOnce("odd");
  await run(f, command("model", "beta", 4));
  expect(f.texts()).toEqual([
    expect.stringContaining("(RPC -32000). Nothing changed."),
    expect.stringContaining("(timeout). Nothing changed."),
    expect.stringContaining("needs codex-cli 0.169.0 or newer"),
    expect.stringContaining("(error). Nothing changed."),
  ]);
  expect(desired(f)).toBeUndefined();
  expect(f.log).toHaveBeenCalledWith("settings.list-failed", {
    code: "timeout",
  });
});

it("tells the running turn keeps its settings and that steer follow-ups join it", async () => {
  const f = settingsFixture();
  f.activeTurn();
  f.store.setCursor("followup-mode", "steer");
  await run(f, command("model", "beta"));
  expect(f.texts()[0]).toContain("The current turn keeps alpha · high.");
  expect(f.texts()[0]).toContain("Steer is on");
  f.currentSettings.mockRejectedValueOnce(new Error("read failed"));
  await run(f, command("effort", "low", 2));
  expect(f.texts()[1]).toContain(
    "The current turn keeps the thread's current setting.",
  );
});

it("commits nothing when another change lands while the list is read", async () => {
  const f = settingsFixture();
  let release: (() => void) | undefined;
  f.listModels.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        release = () => {
          resolve([model("alpha", { isDefault: true }), model("beta")]);
        };
      }),
  );
  const pending = new Commands(f.ctx, { finish: () => false }).run(
    command("model", "beta"),
  );
  await pending;
  await new Promise((resolve) => setImmediate(resolve));
  applySetting(
    f.ctx,
    { updateId: 9, chatId: "1", userId: "1", sentAt: "" },
    "harness",
    {},
  );
  release?.();
  await settingsIdle(f.ctx);
  expect(desired(f)).toBe("{}");
  expect(f.texts()[0]).toContain("changed while this command waited");
});

it("mentions /model and /effort in help, in both languages", async () => {
  for (const language of ["en", "ru"] as const) {
    const f = settingsFixture();
    await new Commands(f.ctx, { finish: () => false }).run({
      ...command("model"),
      command: "help",
      language,
    });
    expect(f.texts()[0]).toMatch(/\/model.*\/effort/u);
  }
});
