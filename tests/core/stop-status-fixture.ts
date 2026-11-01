import { vi } from "vitest";
import { TelegramChannel, TransientManager } from "../../src/channels/index.js";
import type { ChannelEvent } from "../../src/channels/index.js";
import { parseConfig } from "../../src/core/config.js";
import type { RuntimeContext } from "../../src/core/context.js";
import { Delivery } from "../../src/core/delivery.js";
import { TurnEngine } from "../../src/core/engine.js";
import { openStore } from "../../src/store/index.js";

class PreviewApi {
  readonly calls: string[] = [];
  readonly messages = new Map<number, unknown>();
  visible = false;
  holdDraft = false;
  finalFailures = 0;
  release: (() => void) | undefined;
  private nextId = 100;
  readonly fetch: typeof fetch = (input, init) => {
    const url =
      typeof input === "string"
        ? input
        : input instanceof URL
          ? input.href
          : input.url;
    const method = url.split("/").at(-1) ?? "";
    const body = JSON.parse(
      typeof init?.body === "string" ? init.body : "{}",
    ) as Record<string, unknown>;
    this.calls.push(method);
    if (method === "sendRichMessageDraft") {
      const apply = () => {
        this.visible = true;
        return Response.json({ ok: true, result: true });
      };
      if (!this.holdDraft) return Promise.resolve(apply());
      this.holdDraft = false;
      return new Promise<Response>((resolve) => {
        this.release = () => {
          resolve(apply());
        };
      });
    }
    if (method === "sendRichMessage") {
      if (this.finalFailures-- > 0)
        return Promise.resolve(
          Response.json({
            ok: false,
            error_code: 429,
            description: "wait",
            parameters: { retry_after: 2 },
          }),
        );
      this.visible = false;
      const id = ++this.nextId;
      this.messages.set(id, body);
      return Promise.resolve(
        Response.json({ ok: true, result: { message_id: id } }),
      );
    }
    if (method === "deleteMessage")
      this.messages.delete(Number(body.message_id));
    return Promise.resolve(Response.json({ ok: true, result: true }));
  };
}

function seedTurn(
  store: ReturnType<typeof openStore>,
  now: () => Date,
): string {
  const id = "a0000000-0000-4000-8000-000000000001";
  store.putEntity({
    id: "e",
    botId: "123",
    directory: "/tmp/stop-test",
    createdAt: now().toISOString(),
  });
  store.putSession({
    id: "s",
    entityId: "e",
    harness: "codex",
    externalId: "s",
    startedAt: now().toISOString(),
    endedAt: null,
  });
  store.createTurn({
    id,
    entityId: "e",
    source: "telegram",
    createdAt: now().toISOString(),
  });
  store.transitionTurn(id, "queued", "active", {
    sessionId: "s",
    externalTurnId: "turn",
  });
  store.setCursor("chat-id", "1");
  return id;
}

function stopCommand(now: Date): ChannelEvent {
  return {
    kind: "command",
    command: "stop",
    arguments: "",
    updateId: 1,
    messageId: 1,
    chatId: "1",
    userId: "1",
    sentAt: now.toISOString(),
    language: "en",
  };
}

export function setup() {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-11-01T12:00:00Z"));
  const now = () => new Date();
  const store = openStore(":memory:");
  const api = new PreviewApi();
  const channel = new TelegramChannel({
    token: "123:synthetic",
    allowedUserIds: [1],
    fetch: api.fetch,
  });
  const id = seedTurn(store, now);
  const interrupt = vi.fn().mockResolvedValue(undefined);
  const context = {
    store,
    now,
    channel,
    entityId: "e",
    sessionId: "s",
    adapterReady: true,
    config: parseConfig({
      token: { env: "TOKEN" },
      allowlist: [1],
      sandbox: "read-only",
    }),
    transient: new TransientManager(channel, store, now),
    adapter: { capabilities: new Set(), interrupt },
    persistAdapter: () => undefined,
    log: () => undefined,
  } as unknown as RuntimeContext;
  const engine = new TurnEngine(context, new Delivery(context));
  const progress = () =>
    engine.event({
      kind: "progress",
      sessionId: "s",
      turnId: "turn",
      correlationId: id,
      cursor: "p",
      data: { kind: "thinking" },
    });
  const command = stopCommand(now());
  return { store, api, engine, interrupt, progress, command, id, context };
}
