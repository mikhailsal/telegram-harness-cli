import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, expect, it, vi } from "vitest";
import type {
  AdapterInput,
  HarnessModel,
} from "../../src/adapters/contract.js";
import type { ChannelEvent } from "../../src/channels/index.js";
import { parseConfig } from "../../src/core/config.js";
import type { RuntimeContext } from "../../src/core/context.js";
import { Delivery } from "../../src/core/delivery.js";
import { TurnEngine } from "../../src/core/engine.js";
import { settingsIdle } from "../../src/core/harness-settings-runner.js";
import { knownCommands, normalizeEvent } from "../../src/core/queue.js";
import { registerMenus } from "../../src/core/runtime-menu.js";
import { applySetting } from "../../src/core/settings-audit.js";
import { TurnStatuses } from "../../src/core/status.js";
import { openStore } from "../../src/store/index.js";
import {
  catalogModels,
  command,
  decision,
  seedStore,
  telegramFakes,
} from "./harness-settings-fixture.js";

const roots: string[] = [];
afterEach(() => {
  for (const root of roots.splice(0))
    rmSync(root, { recursive: true, force: true });
});
function engineAdapter() {
  let turn = 0;
  return {
    capabilities: new Set(["submit", "observe", "interrupt", "configure"]),
    submit: vi.fn<(input: AdapterInput) => Promise<{ turnId: string }>>(() =>
      Promise.resolve({ turnId: `turn-${String(++turn)}` }),
    ),
    listModels: vi
      .fn<(signal: AbortSignal) => Promise<HarnessModel[]>>()
      .mockResolvedValue(catalogModels()),
    currentSettings: vi.fn().mockResolvedValue({ model: "alpha" }),
    interrupt: vi.fn().mockResolvedValue(undefined),
  };
}
function setup() {
  const root = mkdtempSync(join(tmpdir(), "thc-settings-engine-"));
  roots.push(root);
  const store = openStore(join(root, "store.sqlite"));
  seedStore(store, root);
  const telegram = telegramFakes(store);
  const adapter = engineAdapter();
  let delivered = 1000;
  const context = {
    directory: root,
    config: parseConfig({
      token: { env: "TOKEN" },
      allowlist: [1],
      sandbox: "read-only",
      status: "off",
    }),
    store,
    adapter,
    entityId: "entity",
    sessionId: "session",
    adapterReady: true,
    now: () => new Date("2026-11-16T00:00:00Z"),
    channel: {
      deliverChunk: vi.fn((_chat: string, chunk: { plain: string }) =>
        Promise.resolve({
          messageIds: [++delivered],
          visibleText: chunk.plain,
        }),
      ),
      answerCallback: telegram.answerCallback,
      bot: {
        api: {
          sendMessage: telegram.sendMessage,
          editMessageText: telegram.editMessageText,
        },
      },
    },
    transient: {
      ...telegram.transient,
      sweep: vi.fn().mockResolvedValue(undefined),
      remove: vi.fn().mockResolvedValue(undefined),
    },
    signal: new AbortController().signal,
    log: vi.fn(),
    persistAdapter: vi.fn(),
  } as unknown as RuntimeContext;
  const engine = new TurnEngine(context, new Delivery(context));
  return { store, ...adapter, ...telegram, adapter, context, engine };
}
function message(updateId: number, text: string): ChannelEvent {
  return {
    kind: "message",
    updateId,
    text,
    attachments: [],
    chatId: "1",
    userId: "1",
    messageId: updateId,
    sentAt: "2026-11-16T00:00:00.000Z",
    language: "en",
  };
}
const audit = { updateId: 900, chatId: "1", userId: "1", sentAt: "" };

it("routes every menu command through ingress as a command, /model and /effort included", async () => {
  const setMyCommands = vi.fn().mockResolvedValue(true);
  await registerMenus({
    channel: { bot: { api: { setMyCommands } } },
    signal: new AbortController().signal,
    log: vi.fn(),
  } as unknown as RuntimeContext);
  const menu = (setMyCommands.mock.calls[0]?.[0] as { command: string }[]).map(
    (entry) => entry.command,
  );
  expect(menu).toEqual(expect.arrayContaining(["model", "effort"]));
  for (const name of menu) expect(knownCommands.has(name)).toBe(true);
  expect(normalizeEvent(command("model", "beta"))).toMatchObject({
    kind: "command",
    command: "model",
  });
});

// Value: protects=a turn sent with a fast tier id records serviceTier priority, one without records none, and the sent cursor holds the tier; fails_when=the tier is dropped or mislabelled in the record or cursor; why_new=no test asserted serviceTier in the execution record or sent cursor; seam=none; no_claim=Codex honouring the tier (adapter is a mock)
it("sends the desired settings on every turn and records exactly what was sent", async () => {
  const f = setup();
  await f.engine.accept([message(1, "plain")], 2);
  expect(f.submit.mock.calls[0]?.[0]).not.toHaveProperty("settings");
  await f.engine.event({
    kind: "turn-completed",
    sessionId: "thread",
    turnId: "turn-1",
    cursor: "c1",
    text: "ok",
  });
  applySetting(f.context, audit, "harness", {
    model: "beta",
    effort: "low",
    serviceTier: "priority",
  });
  await f.engine.accept([message(3, "switched")], 4);
  expect(f.submit.mock.calls[1]?.[0].settings).toEqual({
    model: "beta",
    effort: "low",
    serviceTier: "priority",
  });
  const executions = f.store
    .listTurns(["active", "completed"])
    .map(
      (turn) =>
        JSON.parse(
          f.store.getCursor(`turn-execution/${turn.id}/submit`) ?? "{}",
        ) as { model: string; effort: string; serviceTier: string },
    );
  expect(
    executions.map((item) => [item.model, item.effort, item.serviceTier]),
  ).toEqual(
    expect.arrayContaining([
      ["none", "none", "none"],
      ["beta", "low", "priority"],
    ]),
  );
  expect(f.store.getCursor("harness-settings/sent")).toBe(
    JSON.stringify({ model: "beta", effort: "low", serviceTier: "priority" }),
  );
});

// Value: protects=a turn sent after /fast off records serviceTier standard and passes null to the adapter; fails_when=a null tier is recorded raw, as none or dropped; why_new=the null tier branch of recordExecution had no test; seam=none; no_claim=the /fast command flow (state is set directly)
it("records the standard tier for a turn sent after fast mode is turned off", async () => {
  const f = setup();
  applySetting(f.context, audit, "harness", { serviceTier: null });
  await f.engine.accept([message(1, "plain")], 2);
  expect(f.submit.mock.calls[0]?.[0].settings).toEqual({ serviceTier: null });
  const [turn] = f.store.listTurns(["active", "submitting", "completed"]);
  const record = JSON.parse(
    f.store.getCursor(`turn-execution/${turn?.id ?? ""}/submit`) ?? "{}",
  ) as { serviceTier: string };
  expect(record.serviceTier).toBe("standard");
});

it("records what a turn is sent with before its first status frame", async () => {
  const f = setup();
  const seen: (string | undefined)[] = [];
  const refresh = vi
    .spyOn(TurnStatuses.prototype, "refresh")
    .mockImplementation(() => {
      const [turn] = f.store.listTurns(["submitting"]);
      if (turn)
        seen.push(f.store.getCursor(`turn-execution/${turn.id}/submit`));
    });
  try {
    applySetting(f.context, audit, "harness", { model: "beta" });
    await f.engine.accept([message(1, "plain")], 2);
    // protects=the first status frame can name the settings sent; fails_when=the execution record is written after the first refresh; no_claim=the rendered text (status.test covers it)
    expect(seen[0]).toContain('"model":"beta"');
  } finally {
    refresh.mockRestore();
  }
});

it("holds queued turns after modelNotFound until the owner taps recover", async () => {
  const f = setup();
  applySetting(f.context, audit, "harness", { model: "retired" });
  await f.engine.accept([message(1, "first"), message(2, "queued")], 3);
  expect(f.submit).toHaveBeenCalledTimes(1);
  await f.engine.event({
    kind: "turn-error",
    sessionId: "thread",
    turnId: "turn-1",
    cursor: "turn-error/turn-1",
    data: { code: "modelNotFound" },
  });
  await settingsIdle(f.context);
  expect(f.store.getCursor("adapter-cursor")).toBeUndefined();
  await f.engine.event({
    kind: "turn-completed",
    sessionId: "thread",
    turnId: "turn-1",
    cursor: "c1",
    data: { status: "failed" },
  });
  expect(f.submit).toHaveBeenCalledTimes(1);
  expect(f.context.adapterReady).toBe(true);
  // Message ids count from 100 in the fixture.
  const index = f.sendMessage.mock.calls.findIndex((call) =>
    call[1].includes("retired is not available"),
  );
  expect(index).toBeGreaterThanOrEqual(0);
  await f.engine.accept([decision("recover", 100 + index, 4)], 5);
  await settingsIdle(f.context);
  expect(f.submit).toHaveBeenCalledTimes(2);
  expect(f.submit.mock.calls[1]?.[0].settings).toEqual({
    model: "alpha",
    effort: "medium",
  });
});

it("processes /stop at once while a model list read is still pending", async () => {
  const f = setup();
  await f.engine.accept([message(1, "long task")], 2);
  let release: (() => void) | undefined;
  f.listModels.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        release = () => {
          resolve(catalogModels());
        };
      }),
  );
  await f.engine.accept([command("model", "", 3)], 4);
  await f.engine.accept(
    [command("model", "", 5)].map((event) => ({ ...event, command: "stop" })),
    6,
  );
  expect(f.adapter.interrupt).toHaveBeenCalledWith("turn-1");
  release?.();
  await settingsIdle(f.context);
  expect(
    f.sendMessage.mock.calls.some((call) => call[1].includes("Pick a model")),
  ).toBe(true);
});
