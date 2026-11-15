import { createServer } from "node:http";
import { mkdir } from "node:fs/promises";
import { dirname } from "node:path";
import { WebSocketServer, type RawData, type WebSocket } from "ws";

interface Request {
  id?: string | number;
  method?: string;
  params?: Record<string, unknown>;
}
interface Turn {
  id: string;
  status: string;
  items: Record<string, unknown>[];
}

// A Codex app-server on its control socket that answers every turn at once
// with a fixed agent message, enough for the bridge to deliver a first answer.
export class FakeCodexAppServer {
  readonly turns: Turn[] = [];
  readonly calls: Request[] = [];
  private readonly server = createServer();
  private readonly sockets = new WebSocketServer({ server: this.server });
  private readonly answer: string;
  constructor(answer: string) {
    this.answer = answer;
    this.sockets.on("connection", (socket) => {
      socket.on("message", (data) => {
        this.receive(socket, data);
      });
    });
  }
  async start(path: string): Promise<void> {
    await mkdir(dirname(path), { recursive: true });
    await new Promise<void>((resolve, reject) => {
      this.server.once("error", reject);
      this.server.listen(path, resolve);
    });
  }
  private receive(socket: WebSocket, data: RawData): void {
    const bytes = Array.isArray(data)
      ? Buffer.concat(data)
      : Buffer.from(data as ArrayBuffer);
    const request = JSON.parse(bytes.toString("utf8")) as Request;
    this.calls.push(request);
    if (request.id === undefined || !request.method) return;
    const result = this.result(request);
    socket.send(JSON.stringify({ id: request.id, result }));
    const turn = this.turns.at(-1);
    if (request.method === "turn/start" && turn)
      socket.send(
        JSON.stringify({
          method: "turn/completed",
          params: { threadId: "thread-1", turnId: turn.id, turn },
        }),
      );
  }
  private result(request: Request): unknown {
    switch (request.method) {
      case "thread/start":
        return { thread: { id: "thread-1" } };
      case "thread/turns/list":
        return { data: this.turns, nextCursor: null };
      case "thread/read":
        return { thread: { status: { type: "idle" } } };
      case "turn/start":
        return { turn: this.startTurn(request.params ?? {}) };
      default:
        return {};
    }
  }
  private startTurn(params: Record<string, unknown>): Turn {
    const id = `turn-${String(this.turns.length + 1)}`;
    const turn: Turn = {
      id,
      status: "completed",
      items: [
        {
          type: "userMessage",
          id: `${id}-input`,
          clientId: params.clientUserMessageId,
          content: params.input,
        },
        { type: "agentMessage", id: `${id}-output`, text: this.answer },
      ],
    };
    this.turns.push(turn);
    return turn;
  }
  async close(): Promise<void> {
    for (const client of this.sockets.clients) client.terminate();
    this.sockets.close();
    await new Promise<void>((resolve) =>
      this.server.close(() => {
        resolve();
      }),
    );
  }
}
