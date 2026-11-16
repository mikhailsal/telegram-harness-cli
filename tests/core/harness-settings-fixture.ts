import { vi } from "vitest";
import type {
  HarnessModel,
  HarnessSettings,
} from "../../src/adapters/contract.js";
import type { ChannelEvent, Language } from "../../src/channels/index.js";
import { parseConfig } from "../../src/core/config.js";
import type { RuntimeContext } from "../../src/core/context.js";
import { openStore, type Store } from "../../src/store/index.js";

type Keyboard = { text: string; callback_data: string }[][];
interface SendOptions {
  reply_markup?: { inline_keyboard: Keyboard };
  entities?: { type: string; offset: number; length: number }[];
}
interface SettingsOptions {
  models?: HarnessModel[];
  running?: HarnessSettings;
  configure?: boolean;
}

export function model(
  id: string,
  options: Partial<HarnessModel> = {},
): HarnessModel {
  return {
    id,
    model: id,
    displayName: id.toUpperCase(),
    description: `${id} for tests`,
    hidden: false,
    isDefault: false,
    defaultReasoningEffort: "medium",
    supportedReasoningEfforts: [
      { reasoningEffort: "low", description: "fast" },
      { reasoningEffort: "medium", description: "balanced" },
      { reasoningEffort: "high", description: "deep" },
    ],
    ...options,
  };
}
export const alpha = () => model("alpha", { isDefault: true });
export const beta = () =>
  model("beta", {
    defaultReasoningEffort: "minimal",
    supportedReasoningEfforts: [
      { reasoningEffort: "minimal", description: "tiny" },
      { reasoningEffort: "low", description: "fast" },
    ],
  });
export const catalogModels = (): HarnessModel[] => [
  alpha(),
  beta(),
  model("ghost", { hidden: true }),
];

export function seedStore(store: Store, directory = "/tmp"): void {
  const at = "2026-11-16T00:00:00Z";
  store.putEntity({ id: "entity", directory, botId: "bot", createdAt: at });
  store.putSession({
    id: "session",
    entityId: "entity",
    harness: "codex",
    externalId: "thread",
    startedAt: at,
    endedAt: null,
  });
}

export function telegramFakes(store: Store) {
  let nextMessage = 100;
  const sendMessage = vi.fn<
    (
      chat: string,
      text: string,
      options?: SendOptions,
    ) => Promise<{ message_id: number }>
  >(() => Promise.resolve({ message_id: nextMessage++ }));
  const transient = {
    create: vi.fn(
      async (
        intent: Parameters<Store["putTransient"]>[0],
        send: () => Promise<number>,
      ) => {
        store.putTransient(intent);
        const id = await send();
        store.setTransientMessage(intent.id, id, intent.createdAt);
        return id;
      },
    ),
    removeKind: vi.fn().mockResolvedValue(undefined),
    send: vi.fn(async (intent: { chatId: string }, text: string) => {
      const sent = await sendMessage(intent.chatId, text);
      return sent.message_id;
    }),
  };
  return {
    sendMessage,
    transient,
    editMessageText: vi.fn().mockResolvedValue(true),
    answerCallback: vi.fn().mockResolvedValue(undefined),
  };
}

function settingsAdapter(options: SettingsOptions) {
  return {
    capabilities: new Set(options.configure === false ? [] : ["configure"]),
    listModels: vi
      .fn<(signal: AbortSignal) => Promise<HarnessModel[]>>()
      .mockResolvedValue(options.models ?? catalogModels()),
    currentSettings: vi
      .fn<(signal: AbortSignal) => Promise<HarnessSettings | undefined>>()
      .mockResolvedValue(options.running ?? { model: "alpha", effort: "high" }),
  };
}

export function settingsFixture(options: SettingsOptions = {}) {
  const store = openStore(":memory:");
  seedStore(store);
  const telegram = telegramFakes(store);
  const adapter = settingsAdapter(options);
  const log = vi.fn();
  const ctx = {
    store,
    config: parseConfig({
      token: { env: "TOKEN" },
      allowlist: [1],
      sandbox: "read-only",
    }),
    entityId: "entity",
    sessionId: "session",
    adapterReady: true,
    adapter,
    signal: new AbortController().signal,
    now: () => new Date("2026-11-16T00:00:00Z"),
    transient: telegram.transient,
    log,
    channel: {
      answerCallback: telegram.answerCallback,
      bot: {
        api: {
          sendMessage: telegram.sendMessage,
          editMessageText: telegram.editMessageText,
        },
      },
    },
  } as unknown as RuntimeContext;
  return {
    ctx,
    store,
    log,
    ...telegram,
    ...adapter,
    ...views(store, telegram.sendMessage),
  };
}

function views(
  store: Store,
  sendMessage: ReturnType<typeof telegramFakes>["sendMessage"],
) {
  return {
    activeTurn() {
      store.createTurn({
        id: "running",
        entityId: "entity",
        source: "telegram",
        createdAt: "2026-11-16T00:00:00Z",
      });
      store.transitionTurn("running", "queued", "active", {
        sessionId: "session",
        externalTurnId: "external",
      });
    },
    // Texts of every message sent, in order.
    texts: () => sendMessage.mock.calls.map((call) => call[1]),
    lastMarkup: () =>
      sendMessage.mock.calls.at(-1)?.[2]?.reply_markup?.inline_keyboard,
    lastEntities: () => sendMessage.mock.calls.at(-1)?.[2]?.entities ?? [],
  };
}

export function command(
  name: "model" | "effort" | "status" | "new",
  args = "",
  updateId = 1,
  language: Language = "en",
): Extract<ChannelEvent, { kind: "command" }> {
  return {
    kind: "command",
    command: name,
    arguments: args,
    updateId,
    messageId: updateId,
    chatId: "1",
    userId: "1",
    sentAt: "2026-11-16T00:00:00Z",
    language,
  };
}
export function decision(
  data: string,
  messageId: number,
  updateId = 50,
): Extract<ChannelEvent, { kind: "decision" }> {
  return {
    kind: "decision",
    data,
    messageId,
    callbackId: `cb-${String(updateId)}`,
    updateId,
    chatId: "1",
    userId: "1",
    sentAt: "2026-11-16T00:00:00Z",
    language: "en",
  };
}
