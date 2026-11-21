import { createServer } from "node:http";
import { mkdtemp, open, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { WebSocketServer, type WebSocket } from "ws";
import { expect, it } from "vitest";
import { SettingsUnsupportedError } from "../../../src/adapters/contract.ts";
import type { AdapterEvent } from "../../../src/adapters/contract.ts";
import { ProtocolAdapter } from "../../../src/adapters/codex-protocol/index.ts";

interface Request {
  id?: string;
  method?: string;
  params?: Record<string, unknown>;
}
type Mode = "normal" | "unsupported" | "stall";
const model = (id: string, hidden = false) => ({
  id,
  model: id,
  displayName: id.toUpperCase(),
  description: `${id} model`,
  hidden,
  isDefault: id === "alpha",
  defaultReasoningEffort: "medium",
  supportedReasoningEfforts: [
    { reasoningEffort: "low", description: "fast" },
    { reasoningEffort: "medium", description: "balanced" },
  ],
  ...(id === "alpha"
    ? {
        serviceTiers: [
          { id: "priority", name: "Fast", description: "2x speed" },
          { id: "flex", name: "Flex" },
        ],
      }
    : {}),
});
function result(request: Request): unknown {
  switch (request.method) {
    case "thread/start":
      return { thread: { id: "session" } };
    case "thread/turns/list":
      return { data: [], nextCursor: null };
    case "thread/read":
      return { thread: { model: "beta", reasoningEffort: "low" } };
    case "turn/start":
      return { turn: { id: "turn-9" } };
    case "model/list":
      return request.params?.cursor
        ? { data: [model("gamma", true)], nextCursor: null }
        : { data: [model("alpha"), model("beta")], nextCursor: "page-2" };
    default:
      return {};
  }
}
function respond(socket: WebSocket, request: Request, mode: Mode): void {
  if (request.id === undefined) return;
  if (request.method === "model/list" && mode === "stall") return;
  if (request.method === "model/list" && mode === "unsupported") {
    socket.send(
      JSON.stringify({
        id: request.id,
        error: { code: -32601, message: "method not found" },
      }),
    );
    return;
  }
  socket.send(JSON.stringify({ id: request.id, result: result(request) }));
}
async function setup(mode: Mode = "normal") {
  const directory = await mkdtemp(join(tmpdir(), "thc-settings-test-"));
  const socketDirectory = await open(directory, "r");
  const path = `/proc/self/fd/${String(socketDirectory.fd)}/socket`;
  const server = createServer();
  const ws = new WebSocketServer({ server });
  const calls: Request[] = [];
  let client: WebSocket | undefined;
  ws.on("connection", (socket) => {
    client = socket;
    socket.on("message", (data) => {
      const request = JSON.parse(
        Buffer.from(data as Buffer).toString("utf8"),
      ) as Request;
      calls.push(request);
      respond(socket, request, mode);
    });
  });
  await new Promise<void>((resolve) => server.listen(path, resolve));
  const adapter = new ProtocolAdapter({ socketPath: path, cwd: directory });
  await adapter.newSession();
  return {
    adapter,
    calls,
    notify: (value: unknown) => client?.send(JSON.stringify(value)),
    async close() {
      await adapter.close();
      for (const socket of ws.clients) socket.terminate();
      await new Promise<void>((resolve) =>
        server.close(() => {
          resolve();
        }),
      );
      await socketDirectory.close();
      await rm(directory, { recursive: true, force: true });
    },
  };
}
const signal = () => AbortSignal.timeout(5000);

it("lists every model page, hidden ones flagged, and reads the thread settings", async () => {
  const fixture = await setup();
  try {
    expect(fixture.adapter.capabilities.has("configure")).toBe(true);
    const models = await fixture.adapter.listModels(signal());
    expect(models.map((item) => [item.id, item.hidden])).toEqual([
      ["alpha", false],
      ["beta", false],
      ["gamma", true],
    ]);
    expect(models[0]?.supportedReasoningEfforts[1]).toEqual({
      reasoningEffort: "medium",
      description: "balanced",
    });
    // Tiers keep Codex's ids; an older Codex without the field lists none.
    expect(models[0]?.serviceTiers).toEqual([
      { id: "priority", name: "Fast", description: "2x speed" },
      { id: "flex", name: "Flex", description: "" },
    ]);
    expect(models[1]?.serviceTiers).toEqual([]);
    const lists = fixture.calls.filter((call) => call.method === "model/list");
    expect(lists.map((call) => call.params?.cursor)).toEqual([
      undefined,
      "page-2",
    ]);
    expect(lists[0]?.params?.includeHidden).toBe(true);
    expect(await fixture.adapter.currentSettings(signal())).toEqual({
      model: "beta",
      effort: "low",
    });
  } finally {
    await fixture.close();
  }
});

it("sends model and effort on turn/start only when settings are given", async () => {
  const fixture = await setup();
  try {
    await fixture.adapter.submit({ correlationId: "a", text: "plain" });
    await fixture.adapter.submit({
      correlationId: "b",
      text: "switched",
      settings: { model: "beta", effort: "low", serviceTier: "priority" },
    });
    await fixture.adapter.submit({
      correlationId: "c",
      text: "standard",
      settings: { serviceTier: null },
    });
    const starts = fixture.calls.filter((call) => call.method === "turn/start");
    expect(starts[0]?.params).not.toHaveProperty("model");
    expect(starts[0]?.params).not.toHaveProperty("effort");
    expect(starts[0]?.params).not.toHaveProperty("serviceTier");
    expect(starts[1]?.params).toMatchObject({
      model: "beta",
      effort: "low",
      serviceTier: "priority",
    });
    // null, not a missing key: Codex keeps a tier until a turn clears it.
    expect(starts[2]?.params).toHaveProperty("serviceTier", null);
  } finally {
    await fixture.close();
  }
});

it("reports method-not-found from model/list as unsupported", async () => {
  const fixture = await setup("unsupported");
  try {
    await expect(fixture.adapter.listModels(signal())).rejects.toBeInstanceOf(
      SettingsUnsupportedError,
    );
  } finally {
    await fixture.close();
  }
});

it("aborts a stalled model/list without dropping the connection", async () => {
  const fixture = await setup("stall");
  try {
    const controller = new AbortController();
    const listing = fixture.adapter.listModels(controller.signal);
    controller.abort();
    await expect(listing).rejects.toThrow("aborted");
    expect(await fixture.adapter.currentSettings(signal())).toEqual({
      model: "beta",
      effort: "low",
    });
    const already = new AbortController();
    already.abort();
    await expect(fixture.adapter.listModels(already.signal)).rejects.toThrow(
      "aborted",
    );
  } finally {
    await fixture.close();
  }
});

it("maps a final turn error to one non-fatal turn-error event", async () => {
  const fixture = await setup();
  const events: AdapterEvent[] = [];
  const iterator = fixture.adapter.observe()[Symbol.asyncIterator]();
  try {
    const error = (willRetry: boolean, info: unknown) => ({
      method: "error",
      params: {
        threadId: "session",
        turnId: "turn-9",
        willRetry,
        error: { message: "model missing", codexErrorInfo: info },
      },
    });
    fixture.notify(error(true, "modelNotFound"));
    fixture.notify(error(false, "modelNotFound"));
    fixture.notify(error(false, "modelNotFound"));
    fixture.notify({
      method: "error",
      params: {
        threadId: "session",
        turnId: "turn-10",
        willRetry: false,
        error: { message: "x", codexErrorInfo: { httpConnectionFailed: {} } },
      },
    });
    fixture.notify({ method: "error", params: { threadId: "session" } });
    for (let index = 0; index < 2; index += 1) {
      const next = await iterator.next();
      if (!next.done) events.push(next.value);
    }
    expect(events).toEqual([
      {
        kind: "turn-error",
        sessionId: "session",
        cursor: "turn-error/turn-9",
        turnId: "turn-9",
        text: "model missing",
        data: { code: "modelNotFound" },
      },
      expect.objectContaining({
        cursor: "turn-error/turn-10",
        data: { code: "other" },
      }),
    ]);
  } finally {
    await fixture.close();
  }
});

it("refuses settings reads before the session is ready", async () => {
  const adapter = new ProtocolAdapter({ socketPath: "/nonexistent", cwd: "/" });
  await expect(adapter.listModels(signal())).rejects.toThrow("not ready");
  await expect(adapter.currentSettings(signal())).rejects.toThrow("not ready");
});
