import { createServer } from "node:http";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { WebSocketServer, type WebSocket } from "ws";
import { expect, it } from "vitest";
import { ProtocolAdapter } from "../../src/adapters/codex-protocol/index.ts";
interface Message {
  id?: string | number;
  method?: string;
  result?: unknown;
  params?: unknown;
}
function respond(
  socket: WebSocket,
  message: Message,
  results: Message[],
  statuses: unknown[],
): void {
  if (!message.method && message.id === 2) {
    results.push(message);
    socket.send(
      JSON.stringify({
        method: "serverRequest/resolved",
        params: { threadId: "session", requestId: 2 },
      }),
    );
  } else if (message.id !== undefined) {
    const result =
      message.method === "thread/turns/list"
        ? { data: [], nextCursor: null }
        : message.method === "thread/read"
          ? {
              thread: {
                status: statuses.shift() ?? { type: "active", activeFlags: [] },
              },
            }
          : {};
    socket.send(JSON.stringify({ id: message.id, result }));
    results.push(message);
  }
}
async function setup() {
  const root = await mkdtemp(join(tmpdir(), "thc-question-"));
  const server = createServer();
  const ws = new WebSocketServer({ server });
  const results: Message[] = [];
  const statuses: unknown[] = [];
  let client: WebSocket | undefined;
  ws.on("connection", (socket) => {
    client = socket;
    socket.on("message", (raw) => {
      const bytes = Array.isArray(raw)
        ? Buffer.concat(raw)
        : raw instanceof ArrayBuffer
          ? Buffer.from(raw)
          : raw;
      respond(
        socket,
        JSON.parse(bytes.toString("utf8")) as Message,
        results,
        statuses,
      );
    });
  });
  const path = join(root, "socket");
  await new Promise<void>((resolve) => server.listen(path, resolve));
  const adapter = new ProtocolAdapter({
    socketPath: path,
    cwd: root,
    sessionId: "session",
  });
  return {
    adapter,
    results,
    statuses,
    client: () => client,
    async close() {
      await adapter.close();
      for (const socket of ws.clients) socket.terminate();
      ws.close();
      await new Promise<void>((resolve, reject) =>
        server.close((error) => {
          if (error) reject(error);
          else resolve();
        }),
      );
      await rm(root, { recursive: true, force: true });
    },
  };
}
it("routes requestUserInput and returns the answers object on the wire", async () => {
  const f = await setup();
  try {
    await f.adapter.reconcile();
    const iterator = f.adapter.observe()[Symbol.asyncIterator]();
    f.client()?.send(
      JSON.stringify({
        method: "item/tool/requestUserInput",
        id: 2,
        params: {
          threadId: "session",
          turnId: "turn",
          questions: [{ id: "q", question: "Pick", options: [] }],
        },
      }),
    );
    expect((await iterator.next()).value).toMatchObject({
      kind: "question-requested",
      requestId: "2",
      turnId: "turn",
    });
    await f.adapter.approve({
      requestId: "2",
      decision: { answers: { q: { answers: ["yes"] } } },
    });
    expect(f.results.find((message) => message.id === 2)?.result).toEqual({
      answers: { q: { answers: ["yes"] } },
    });
    expect(await f.adapter.pendingState()).toBe("none");
    f.statuses.push(
      { type: "active", activeFlags: ["waitingOnUserInput"] },
      { type: "notLoaded" },
      { type: "active", activeFlags: ["futureFlag"] },
    );
    expect(await f.adapter.pendingState()).toBe("some");
    expect(await f.adapter.pendingState()).toBe("unknown");
    expect(await f.adapter.pendingState()).toBe("unknown");
    await f.adapter.steer({ correlationId: "follow", text: "more" }, "turn");
    await f.adapter.compact();
    expect(
      f.results.find((message) => message.method === "turn/steer")?.params,
    ).toMatchObject({ expectedTurnId: "turn", clientUserMessageId: "follow" });
    expect(
      f.results.some((message) => message.method === "thread/compact/start"),
    ).toBe(true);
  } finally {
    await f.close();
  }
});
