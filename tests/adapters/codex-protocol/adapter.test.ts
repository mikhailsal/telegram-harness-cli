import { createServer, type Server } from "node:http";
import { mkdir, mkdtemp, open, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { WebSocketServer, type WebSocket } from "ws";
import { expect, it } from "vitest";
import {
  CompactRejectedError,
  SteerRejectedError,
} from "../../../src/adapters/contract.ts";
import type { AdapterEvent } from "../../../src/adapters/contract.ts";
import { ProtocolAdapter } from "../../../src/adapters/codex-protocol/index.ts";

interface Request {
  id?: string;
  method?: string;
  params?: Record<string, unknown>;
}
const turn = {
  id: "turn-1",
  status: "completed",
  items: [
    {
      type: "userMessage",
      id: "input-1",
      clientId: "correlation-1",
      content: [{ type: "text", text: "hello" }],
    },
    { type: "agentMessage", id: "output-1", text: "world" },
  ],
};
function respond(socket: WebSocket, request: Request): void {
  if (!request.method && request.id !== undefined) {
    socket.send(
      JSON.stringify({
        method: "serverRequest/resolved",
        params: { threadId: "session", requestId: request.id },
      }),
    );
    return;
  }
  if (!request.id) return;
  if (
    request.method === "turn/steer" &&
    request.params?.expectedTurnId === "ended"
  ) {
    socket.send(
      JSON.stringify({
        id: request.id,
        error: { code: -32600, message: "no active turn to steer" },
      }),
    );
    return;
  }
  if (
    request.method === "turn/steer" &&
    request.params?.expectedTurnId === "uncertain"
  ) {
    socket.send(
      JSON.stringify({
        id: request.id,
        error: { code: -32603, message: "no active turn to steer" },
      }),
    );
    return;
  }
  socket.send(
    JSON.stringify({ id: request.id, result: responseResult(request.method) }),
  );
}
function responseResult(method: string | undefined): object {
  switch (method) {
    case "thread/start":
      return { thread: { id: "session" } };
    case "command/exec":
      return { exitCode: 0, stdout: "ok", stderr: "" };
    case "thread/turns/list":
      return { data: [turn], nextCursor: null };
    case "turn/start":
      return { turn: { id: "turn-2" } };
    default:
      return {};
  }
}
async function listen(server: Server, path: string): Promise<void> {
  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(path, () => {
      server.off("error", reject);
      resolve();
    });
  });
}
function rejectCompact(
  socket: WebSocket,
  request: Request,
  code?: number,
): boolean {
  if (request.method !== "thread/compact/start" || code === undefined)
    return false;
  socket.send(
    JSON.stringify({
      id: request.id,
      error: { code, message: "compact error" },
    }),
  );
  return true;
}
async function setup(parent = tmpdir(), compactCode?: number) {
  const directory = await mkdtemp(join(parent, "thc-protocol-test-"));
  // Linux limits Unix-socket names to 107 bytes; the quality snapshot uses a
  // deeply nested TMPDIR, so address the same directory through its short fd path.
  const socketDirectory = await open(directory, "r");
  const path = `/proc/self/fd/${String(socketDirectory.fd)}/socket`;
  const server = createServer();
  const ws = new WebSocketServer({ server });
  const calls: Request[] = [];
  let client: WebSocket | undefined;
  ws.on("connection", (socket) => {
    client = socket;
    socket.on("message", (data) => {
      const bytes = Array.isArray(data)
        ? Buffer.concat(data)
        : data instanceof ArrayBuffer
          ? Buffer.from(data)
          : data;
      const request = JSON.parse(bytes.toString("utf8")) as Request;
      calls.push(request);
      if (rejectCompact(socket, request, compactCode)) return;
      respond(socket, request);
    });
  });
  await listen(server, path);
  const adapter = new ProtocolAdapter({
    socketPath: path,
    cwd: directory,
    sessionId: "session",
    uncertainCorrelationIds: ["uncertain"],
  });
  return {
    adapter,
    calls,
    client: () => client,
    async cleanup() {
      await adapter.close();
      for (const socket of ws.clients) socket.terminate();
      ws.close();
      await new Promise<void>((resolve, reject) =>
        server.close((error) => {
          if (error) reject(error);
          else resolve();
        }),
      );
      await socketDirectory.close();
      await rm(directory, { recursive: true, force: true });
    },
  };
}
it.each([
  [-32600, true],
  [-32603, false],
])("classifies compact RPC error %s conservatively", async (code, definite) => {
  const fixture = await setup(tmpdir(), code);
  try {
    await fixture.adapter.reconcile();
    const outcome = fixture.adapter.compact();
    if (definite)
      await expect(outcome).rejects.toBeInstanceOf(CompactRejectedError);
    else await expect(outcome).rejects.not.toBeInstanceOf(CompactRejectedError);
  } finally {
    await fixture.cleanup();
  }
});
async function checkApproval(
  adapter: ProtocolAdapter,
  client: WebSocket | undefined,
): Promise<void> {
  const iterator = adapter.observe()[Symbol.asyncIterator]();
  client?.send(
    JSON.stringify({
      method: "item/commandExecution/requestApproval",
      id: 7,
      params: { threadId: "session", turnId: "turn-1" },
    }),
  );
  expect((await iterator.next()).value).toMatchObject({
    kind: "approval-requested",
    requestId: "7",
  });
  await adapter.approve({ requestId: "7", decision: "accept" });
  expect((await iterator.next()).value).toMatchObject({
    kind: "approval-resolved",
    requestId: "7",
  });
  await expect(
    adapter.approve({ requestId: "7", decision: "accept" }),
  ).rejects.toThrow("not pending");
}
it("reconciles history, prevents duplicates, and awaits approval acknowledgement", async () => {
  const fixture = await setup();
  const { adapter, calls } = fixture;
  try {
    const events = await adapter.reconcile();
    expect(events.map((event) => event.kind)).toEqual([
      "turn-started",
      "input",
      "output",
      "turn-completed",
    ]);
    await expect(
      adapter.submit({ correlationId: "uncertain", text: "maybe sent" }),
    ).rejects.toThrow("already");
    await expect(
      adapter.submit({ correlationId: "correlation-1", text: "hello" }),
    ).rejects.toThrow("already");
    expect(
      await adapter.submit({ correlationId: "correlation-2", text: "next" }),
    ).toEqual({ turnId: "turn-2" });
    expect(calls.filter((call) => call.method === "turn/start")).toHaveLength(
      1,
    );
    expect(
      calls.find((call) => call.method === "turn/start")?.params
        ?.clientUserMessageId,
    ).toBe("correlation-2");
    await checkApproval(adapter, fixture.client());
    fixture.client()?.terminate();
    await new Promise((resolve) => setTimeout(resolve, 30));
    await expect(
      adapter.submit({ correlationId: "correlation-3", text: "wait" }),
    ).rejects.toThrow("Reconcile");
    const replay = await adapter.reconcile(events[1]?.cursor);
    expect(replay.map((event) => event.kind)).toEqual([
      "output",
      "turn-completed",
    ]);
    expect(
      calls.filter((call) => call.method === "thread/resume"),
    ).toHaveLength(2);
  } finally {
    await fixture.cleanup();
  }
});

it("starts a session, checks cursors, sends explicit command context, and interrupts", async () => {
  const fixture = await setup();
  const { adapter, calls } = fixture;
  try {
    await expect(
      adapter.submit({ correlationId: "x", text: "before ready" }),
    ).rejects.toThrow("Reconcile");
    expect(await adapter.newSession()).toEqual({ id: "session" });
    expect(await adapter.attach()).toMatchObject({
      command: "codex",
      args: [
        "--remote",
        expect.stringContaining("unix://"),
        "resume",
        "session",
      ],
    });
    await expect(
      adapter.commandExec([], {
        cwd: "/tmp",
        env: {},
        sandboxPolicy: { type: "readOnly" },
      }),
    ).rejects.toThrow("required");
    const context = {
      cwd: "/tmp",
      env: { PATH: "/bin" },
      sandboxPolicy: { type: "readOnly" },
    };
    expect(await adapter.commandExec(["true"], context)).toMatchObject({
      exitCode: 0,
      stdout: "ok",
    });
    expect(
      calls.find((call) => call.method === "command/exec")?.params,
    ).toMatchObject(context);
    await adapter.interrupt("turn-1");
    expect(
      calls.find((call) => call.method === "turn/interrupt")?.params,
    ).toMatchObject({ threadId: "session", turnId: "turn-1" });
    await expect(adapter.reconcile("missing-cursor")).rejects.toThrow(
      "cursor not found",
    );
    await expect(
      adapter.submit({ correlationId: "next", text: "wait" }),
    ).rejects.toThrow("Reconcile");
    await adapter.reconcile();
  } finally {
    await fixture.cleanup();
  }
});

it("delivers live turn and item events once using stable history cursors", async () => {
  const fixture = await setup();
  const { adapter } = fixture;
  try {
    await adapter.reconcile();
    const iterator = adapter.observe()[Symbol.asyncIterator]();
    const socket = fixture.client();
    const send = (method: string, params: unknown): void => {
      socket?.send(JSON.stringify({ method, params }));
    };
    send("turn/started", {
      threadId: "session",
      turn: { ...turn, status: "inProgress", items: [] },
    });
    send("item/completed", {
      threadId: "session",
      turnId: "turn-1",
      item: turn.items[0],
    });
    send("item/completed", {
      threadId: "session",
      turnId: "turn-1",
      item: turn.items[1],
    });
    send("turn/completed", { threadId: "session", turn });
    const events: AdapterEvent[] = [];
    for (let i = 0; i < 4; i++) {
      const result = await iterator.next();
      if (result.done) throw new Error("Unexpected end of event stream");
      events.push(result.value);
    }
    expect(events.map((event) => event.kind)).toEqual([
      "turn-started",
      "input",
      "output",
      "turn-completed",
    ]);
    expect(new Set(events.map((event) => event.cursor)).size).toBe(4);
  } finally {
    await fixture.cleanup();
  }
});

it("requeues only a definitive no-active-turn rejection", async () => {
  const fixture = await setup();
  try {
    await fixture.adapter.reconcile();
    await expect(
      fixture.adapter.steer(
        { correlationId: "retryable", text: "followup" },
        "ended",
      ),
    ).rejects.toBeInstanceOf(SteerRejectedError);
    await expect(
      fixture.adapter.steer(
        { correlationId: "retryable", text: "followup" },
        "active",
      ),
    ).resolves.toBeUndefined();
    await expect(
      fixture.adapter.steer(
        { correlationId: "uncertain", text: "followup" },
        "uncertain",
      ),
    ).rejects.not.toBeInstanceOf(SteerRejectedError);
    await expect(
      fixture.adapter.steer(
        { correlationId: "uncertain", text: "followup" },
        "active",
      ),
    ).rejects.toThrow("already");
  } finally {
    await fixture.cleanup();
  }
});

it("connects to an isolated socket when the temporary directory exceeds the Unix path limit", async () => {
  const root = await mkdtemp(join(tmpdir(), "thc-long-socket-"));
  const nested = join(root, "x".repeat(100));
  try {
    await mkdir(nested);
    const fixture = await setup(nested);
    try {
      expect(
        (await fixture.adapter.reconcile()).map((event) => event.kind),
      ).toContain("input");
    } finally {
      await fixture.cleanup();
    }
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
