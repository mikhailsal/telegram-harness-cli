import { expect, it } from "vitest";
import { Commands } from "../../src/core/commands.js";
import {
  settingsHeld,
  settingsIdle,
} from "../../src/core/harness-settings-runner.js";
import {
  applySetting,
  desiredSettings,
} from "../../src/core/settings-audit.js";
import {
  alpha,
  command,
  decision,
  model,
  settingsFixture,
} from "./harness-settings-fixture.js";

async function run(
  f: ReturnType<typeof settingsFixture>,
  event: ReturnType<typeof command>,
): Promise<void> {
  await new Commands(f.ctx, { finish: () => false }).run(event);
  await settingsIdle(f.ctx);
}
const desired = (f: ReturnType<typeof settingsFixture>) =>
  JSON.parse(f.store.getCursor("harness-settings/desired") ?? "{}") as unknown;

it("turns the current model's Fast tier on and off, by toggle or by name", async () => {
  const f = settingsFixture();
  await run(f, command("fast"));
  expect(desired(f)).toEqual({ serviceTier: "priority" });
  expect(f.texts()[0]).toBe(
    "✓ Fast mode on for alpha (Fast: 2x speed, increased usage). Applies from the next turn, including queued messages.",
  );
  await run(f, command("fast", "", 2));
  expect(desired(f)).toEqual({ serviceTier: null });
  expect(f.texts()[1]).toContain("✓ Fast mode off");
  expect(desiredSettings(f.ctx)).toEqual({ serviceTier: null });
  await run(f, command("fast", "ON", 3));
  expect(desired(f)).toEqual({ serviceTier: "priority" });
  f.listModels.mockClear();
  await run(f, command("fast", "off", 4));
  expect(desired(f)).toEqual({ serviceTier: null });
  expect(f.listModels).not.toHaveBeenCalled();
});

it("keeps the tier through model, effort and reset changes, and says so on reset", async () => {
  const f = settingsFixture();
  await run(f, command("fast", "on"));
  await run(f, command("model", "beta", 2));
  expect(desired(f)).toEqual({
    model: "beta",
    effort: "minimal",
    serviceTier: "priority",
  });
  await run(f, command("effort", "low", 3));
  expect(desired(f)).toEqual({
    model: "beta",
    effort: "low",
    serviceTier: "priority",
  });
  await run(f, command("fast", "off", 4));
  expect(desired(f)).toEqual({
    model: "beta",
    effort: "low",
    serviceTier: null,
  });
  await run(f, command("fast", "on", 5));
  await run(f, command("model", "reset", 6));
  expect(desired(f)).toEqual({ serviceTier: "priority" });
  expect(f.texts()[5]).toContain("Fast mode stays on; /fast off turns it off.");
});

// Value: protects=a model change that keeps fast mode on says so when the new model does not list the tier, by command and by card, and stays quiet when it does; fails_when=the notice is missing, shown for a listing model or dropped on the card path; why_new=no test read the model confirmation with fast mode on; seam=none; no_claim=what Codex does with an unlisted tier
it("warns when the new model does not list the kept fast tier", async () => {
  const f = settingsFixture({
    models: [alpha(), model("beta", { serviceTiers: [] })],
  });
  await run(f, command("fast", "on"));
  await run(f, command("model", "beta", 2));
  const notice =
    "beta does not list the priority tier, but fast mode stays on and each turn still asks for it; /fast off turns it off.";
  expect(f.texts()[1]).toContain(notice);
  await run(f, command("model", "alpha", 3));
  expect(f.texts()[2]).not.toContain("does not list");
  await run(f, command("model", "", 4));
  await new Commands(f.ctx, { finish: () => false }).decision(
    decision("model:1", 103, 50),
  );
  await settingsIdle(f.ctx);
  expect(String(f.editMessageText.mock.lastCall?.[2])).toContain(notice);
});

// Value: protects=a modelNotFound queue hold survives /fast, which keeps the rejected model, and lifts on a model change; fails_when=a /fast write lifts the hold and the next queued message is sent to the rejected model, fails and is not resent; why_new=no test changed fast mode while the queue was held; seam=none; no_claim=the recover tap (harness-settings-cards covers it)
it("keeps the queue held after modelNotFound when only fast mode changes", async () => {
  const f = settingsFixture();
  const audit = { updateId: 1, chatId: "1", userId: "1", sentAt: "" };
  applySetting(f.ctx, audit, "harness", { model: "retired" });
  f.store.setCursor("settings-blocked", "retired");
  await run(f, command("fast", "off", 2));
  expect(desired(f)).toEqual({ model: "retired", serviceTier: null });
  expect(settingsHeld(f.ctx)).toBe(true);
  await run(f, command("model", "beta", 3));
  expect(settingsHeld(f.ctx)).toBe(false);
  expect(f.store.getCursor("settings-blocked")).toBe("");
});

it("refuses a model without tiers and an unknown argument, changing nothing", async () => {
  const f = settingsFixture({
    models: [{ ...alpha(), serviceTiers: [] }, model("beta")],
  });
  await run(f, command("fast", "on"));
  expect(f.texts()[0]).toBe("alpha offers no fast tier. Nothing changed.");
  await run(f, command("fast", "turbo", 2));
  expect(f.texts()[1]).toBe(
    '"turbo" is not a /fast option. Use /fast, /fast on or /fast off. Nothing changed.',
  );
  expect(f.store.getCursor("harness-settings/desired")).toBeUndefined();
});

it("picks the tier Codex names Fast over the first one, in Russian too", async () => {
  const f = settingsFixture({
    models: [
      model("alpha", {
        isDefault: true,
        serviceTiers: [
          { id: "flex", name: "Flex", description: "" },
          { id: "turbo", name: "fast", description: "" },
        ],
      }),
    ],
  });
  await run(f, command("fast", "", 1, "ru"));
  expect(desired(f)).toEqual({ serviceTier: "turbo" });
  expect(f.texts()[0]).toBe(
    "✓ Быстрый режим для alpha включён (fast). Действует со следующей задачи, включая сообщения в очереди.",
  );
  f.activeTurn();
  await run(f, command("fast", "", 2, "ru"));
  expect(f.texts()[1]).toContain("Текущая задача продолжает с alpha · high.");
});

it("replies without any RPC on tmux", async () => {
  const tmux = settingsFixture({ configure: false });
  await run(tmux, command("fast"));
  expect(tmux.texts()[0]).toContain("needs the protocol adapter");
  expect(tmux.listModels).not.toHaveBeenCalled();
});
