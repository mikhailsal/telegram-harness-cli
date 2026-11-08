import WebSocket from "ws";
import { z } from "zod";

const messageSchema = z
  .object({
    id: z.union([z.string(), z.number()]).optional(),
    method: z.string().optional(),
    params: z.unknown().optional(),
    result: z.unknown().optional(),
    error: z
      .object({ message: z.string(), code: z.number().optional() })
      .loose()
      .optional(),
  })
  .loose();
type RpcMessage = z.infer<typeof messageSchema>;
interface Pending {
  resolve: (value: unknown) => void;
  reject: (reason: Error) => void;
  timer: NodeJS.Timeout;
}

export class RpcError extends Error {
  readonly code: number | undefined;
  constructor(message: string, code: number | undefined) {
    super(message);
    this.name = "RpcError";
    this.code = code;
  }
}
export class ProtocolTransport {
  private socket: WebSocket | undefined;
  private sequence = 0;
  private readonly pending = new Map<string, Pending>();
  private readonly onMessage: (message: RpcMessage) => void;
  private readonly onDisconnect: () => void;
  constructor(
    onMessage: (message: RpcMessage) => void,
    onDisconnect: () => void,
  ) {
    this.onMessage = onMessage;
    this.onDisconnect = onDisconnect;
  }
  async connect(path: string): Promise<void> {
    if (this.socket) throw new Error("Already connected");
    const socket = new WebSocket(`ws+unix://${path}:/`);
    this.socket = socket;
    socket.on("message", (data) => {
      this.receive(
        Buffer.concat(
          Array.isArray(data)
            ? data
            : [data instanceof ArrayBuffer ? Buffer.from(data) : data],
        ).toString("utf8"),
      );
    });
    socket.on("close", () => {
      this.disconnected(socket);
    });
    socket.on("error", () => {
      socket.terminate();
    });
    await new Promise<void>((resolve, reject) => {
      const timer = setTimeout(() => {
        reject(new Error("Protocol connection timed out"));
        socket.terminate();
      }, 30_000);
      socket.once("open", () => {
        clearTimeout(timer);
        resolve();
      });
      socket.once("error", (error) => {
        clearTimeout(timer);
        reject(error);
      });
    });
    await this.call("initialize", {
      clientInfo: { name: "telegram-harness-cli", version: "0.0.0" },
      capabilities: { experimentalApi: true },
    });
    this.send({ method: "initialized", params: {} });
  }
  async call(method: string, params: unknown): Promise<unknown> {
    const id = String(++this.sequence);
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error("Protocol request timed out; outcome may be unknown"));
        this.socket?.terminate();
      }, 30_000);
      this.pending.set(id, { resolve, reject, timer });
      try {
        this.send({ id, method, params });
      } catch (error) {
        clearTimeout(timer);
        this.pending.delete(id);
        reject(error instanceof Error ? error : new Error(String(error)));
      }
    });
  }
  send(value: unknown): void {
    if (this.socket?.readyState !== WebSocket.OPEN)
      throw new Error("Protocol disconnected");
    this.socket.send(JSON.stringify(value));
  }
  close(): void {
    this.socket?.close();
  }
  private receive(raw: string): void {
    let value: unknown;
    try {
      value = JSON.parse(raw) as unknown;
    } catch {
      return;
    }
    const parsed = messageSchema.safeParse(value);
    if (!parsed.success) return;
    const message = parsed.data;
    if (message.id !== undefined && !message.method) {
      const pending = this.pending.get(String(message.id));
      if (pending) {
        this.pending.delete(String(message.id));
        clearTimeout(pending.timer);
        if (message.error)
          pending.reject(
            new RpcError(message.error.message, message.error.code),
          );
        else pending.resolve(message.result);
      }
    } else this.onMessage(message);
  }
  private disconnected(socket: WebSocket): void {
    if (this.socket !== socket) return;
    this.socket = undefined;
    for (const item of this.pending.values()) {
      clearTimeout(item.timer);
      item.reject(new Error("Protocol disconnected; outcome may be unknown"));
    }
    this.pending.clear();
    this.onDisconnect();
  }
}
