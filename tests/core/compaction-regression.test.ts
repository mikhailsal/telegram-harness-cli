import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, it, vi } from "vitest";
import type { AdapterEvent } from "../../src/adapters/contract.js";
import { parseConfig } from "../../src/core/config.js";
import type { RuntimeContext } from "../../src/core/context.js";
import { Delivery } from "../../src/core/delivery.js";
import { TurnEngine } from "../../src/core/engine.js";
import { openStore, type Store } from "../../src/store/index.js";

const now = () => new Date("2026-11-02T12:00:00Z");
function seed(store: Store, directory: string): void {
  store.putEntity({
    id: "e",
    directory,
    botId: "bot",
    createdAt: now().toISOString(),
  });
  store.putSession({
    id: "s",
    entityId: "e",
    harness: "codex",
    externalId: "thread",
    startedAt: now().toISOString(),
    endedAt: null,
  });
  store.setCursor("chat-id", "1");
}
function makeContext(
  directory: string,
  store: Store,
  delivered: string[],
): RuntimeContext {
  return {
    directory,
    store,
    entityId: "e",
    sessionId: "s",
    adapterReady: true,
    now,
    config: parseConfig({
      token: { env: "TOKEN" },
      allowlist: [1],
      sandbox: "read-only",
      status: "off",
    }),
    adapter: { capabilities: new Set() },
    persistAdapter: () => undefined,
    log: () => undefined,
    channel: {
      deliverChunk: (_chat: string, chunk: { plain: string }) => {
        delivered.push(chunk.plain);
        return Promise.resolve({
          messageIds: [100 + delivered.length],
          visibleText: chunk.plain,
        });
      },
    },
    transient: {
      removeKind: vi.fn().mockResolvedValue(undefined),
      remove: vi.fn().mockResolvedValue(undefined),
      sweep: vi.fn().mockResolvedValue(undefined),
    },
  } as unknown as RuntimeContext;
}
function fixture() {
  const directory = mkdtempSync(join(tmpdir(), "thc-compact-core-"));
  const path = join(directory, "db");
  let store = openStore(path);
  const delivered: string[] = [];
  seed(store, directory);
  const context = () => makeContext(directory, store, delivered);
  let engine = new TurnEngine(context(), new Delivery(context()));
  const event = (
    kind: AdapterEvent["kind"],
    extra: Partial<AdapterEvent> = {},
  ) =>
    engine.event({
      kind,
      sessionId: "thread",
      turnId: "external",
      cursor: `${kind}/${extra.text ?? ""}`,
      ...extra,
    });
  return {
    get store() {
      return store;
    },
    delivered,
    event,
    restart: async () => {
      await engine.close();
      store.close();
      store = openStore(path);
      const ctx = context();
      engine = new TurnEngine(ctx, new Delivery(ctx));
    },
    close: async () => {
      await engine.close();
      store.close();
      rmSync(directory, { recursive: true, force: true });
    },
  };
}
async function startCompact(f: ReturnType<typeof fixture>): Promise<void> {
  await f.event("turn-started");
  await f.event("progress", { data: { kind: "compacting" } });
}
it("silently completes standalone compact while retaining the classification across restart", async () => {
  const f = fixture();
  try {
    await startCompact(f);
    await f.restart();
    await f.event("turn-completed", { data: { status: "completed" } });
    expect(f.delivered).toEqual([]);
    expect(f.store.listTurns(["completed"])).toHaveLength(1);
    expect(f.store.listOutbox(["pending", "sent", "failed"])).toEqual([]);
  } finally {
    await f.close();
  }
});
it("reports failed standalone compaction", async () => {
  const f = fixture();
  try {
    await startCompact(f);
    await f.event("turn-completed", { data: { status: "failed" } });
    expect(f.delivered).toEqual([expect.stringContaining("compaction failed")]);
  } finally {
    await f.close();
  }
});
it("keeps a real terminal task answer even if the turn compacts", async () => {
  const f = fixture();
  try {
    await f.event("turn-started");
    await f.event("progress", { data: { kind: "compacting" } });
    await f.event("input", { text: "real terminal task" });
    await f.event("output", { text: "terminal answer" });
    await f.event("turn-completed", { data: { status: "completed" } });
    expect(f.delivered.join(" ")).toContain("terminal answer");
  } finally {
    await f.close();
  }
});
it("keeps output from a terminal turn even if no input event was observed", async () => {
  const f = fixture();
  try {
    await startCompact(f);
    await f.event("output", { text: "visible terminal output" });
    await f.event("turn-completed", { data: { status: "completed" } });
    expect(f.delivered.join(" ")).toContain("visible terminal output");
  } finally {
    await f.close();
  }
});
it("fails open when the beginning of a terminal turn was not observed", async () => {
  const f = fixture();
  try {
    f.store.createTurn({
      id: "prior",
      entityId: "e",
      source: "terminal",
      createdAt: "2026-11-02T12:00:00Z",
    });
    f.store.transitionTurn("prior", "queued", "active", {
      sessionId: "s",
      externalTurnId: "external",
    });
    await f.event("progress", { data: { kind: "compacting" } });
    await f.event("turn-completed", { data: { status: "completed" } });
    expect(f.delivered).toHaveLength(1);
  } finally {
    await f.close();
  }
});
it("keeps a Telegram task answer when it auto-compacts", async () => {
  const f = fixture();
  try {
    f.store.createTurn({
      id: "tg",
      entityId: "e",
      source: "telegram",
      createdAt: "2026-11-02T12:00:00Z",
    });
    f.store.transitionTurn("tg", "queued", "active", {
      sessionId: "s",
      externalTurnId: "external",
    });
    await f.event("progress", { data: { kind: "compacting" } });
    await f.event("output", { text: "telegram answer" });
    await f.event("turn-completed", { data: { status: "completed" } });
    expect(f.delivered).toEqual(["telegram answer"]);
  } finally {
    await f.close();
  }
});
