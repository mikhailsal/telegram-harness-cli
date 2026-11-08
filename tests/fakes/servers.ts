import {
  createServer,
  type IncomingMessage,
  type ServerResponse,
} from "node:http";
import { once } from "node:events";

interface FakeEvent {
  kind: string;
  sessionId: string;
  cursor: string;
  turnId: string;
  correlationId?: string;
  requestId?: string;
  text?: string;
  data?: unknown;
}
export class FakeServices {
  readonly updates: object[] = [];
  readonly submissions: { correlationId: string; text: string }[] = [];
  readonly events: FakeEvent[] = [];
  readonly decisions: Record<string, unknown>[] = [];
  readonly delivered: { method: string; body: Record<string, unknown> }[] = [];
  readonly calls: { method: string; body: Record<string, unknown> }[] = [];
  downloads = 0;
  holdDownload = false;
  holdSubmit = false;
  holdDelivery = false;
  private readonly held: ServerResponse[] = [];
  private readonly server = createServer((req, res) => {
    void this.handle(req, res);
  });
  url = "";
  async start(): Promise<void> {
    this.server.listen(0, "127.0.0.1");
    await once(this.server, "listening");
    const address = this.server.address();
    if (!address || typeof address === "string")
      throw new Error("Missing local server address");
    this.url = `http://127.0.0.1:${String(address.port)}`;
  }
  private async handle(
    req: IncomingMessage,
    res: ServerResponse,
  ): Promise<void> {
    const chunks: Buffer[] = [];
    for await (const chunk of req)
      chunks.push(Buffer.from(chunk as Uint8Array));
    const body = JSON.parse(Buffer.concat(chunks).toString() || "{}") as Record<
      string,
      unknown
    >;
    const path = req.url ?? "";
    if (this.harness(path, body, res)) return;
    this.bot(path, body, res);
  }
  private harness(
    path: string,
    body: Record<string, unknown>,
    res: ServerResponse,
  ): boolean {
    if (path === "/harness/submit") {
      this.submissions.push(
        body as unknown as { correlationId: string; text: string },
      );
      if (this.holdSubmit) {
        this.held.push(res);
        return true;
      }
      this.json(res, {
        turnId: `turn-${String(this.submissions.length)}`,
      });
      return true;
    }
    if (path === "/harness/approve") {
      this.decisions.push(body);
      this.json(res, {});
      return true;
    }
    if (path === "/harness/events") {
      this.json(res, {
        events: this.events.filter(
          (event) => Number(event.cursor) > Number(body.cursor),
        ),
      });
      return true;
    }
    return false;
  }
  private bot(
    path: string,
    body: Record<string, unknown>,
    res: ServerResponse,
  ): void {
    const method = path.split("/").at(-1) ?? "";
    this.calls.push({ method, body });
    if (path.startsWith("/file/bot")) {
      this.download(res);
      return;
    }
    if (method === "getFile") {
      this.ok(res, {
        file_id: "file-1",
        file_unique_id: "unique-1",
        file_path: "synthetic.dat",
      });
      return;
    }
    if (method === "getUpdates") {
      this.ok(
        res,
        this.updates.filter(
          (update) =>
            (update as { update_id: number }).update_id >=
            Number(body.offset ?? 0),
        ),
      );
      return;
    }
    if (method === "sendRichMessage" || method === "sendMessage") {
      this.delivered.push({ method, body });
      if (this.holdDelivery && method === "sendRichMessage") {
        this.held.push(res);
        return;
      }
      this.ok(res, {
        message_id: 100 + this.delivered.length,
        date: 1,
        chat: { id: 1, type: "private" },
      });
      return;
    }
    this.ok(res, true);
  }
  private download(res: ServerResponse): void {
    this.downloads++;
    res.writeHead(200, { "content-type": "application/octet-stream" });
    if (this.holdDownload) {
      res.write("partial");
      this.held.push(res);
      return;
    }
    res.end("complete attachment");
  }
  private json(res: ServerResponse, value: unknown): void {
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify(value));
  }
  private ok(res: ServerResponse, value: unknown): void {
    this.json(res, { ok: true, result: value });
  }
  input(id: number, text: string): void {
    this.updates.push({
      update_id: id,
      message: {
        message_id: id,
        date: 1760000000,
        chat: { id: 1, type: "private" },
        from: {
          id: 1,
          is_bot: false,
          first_name: "Tester",
          language_code: "en",
        },
        text,
      },
    });
  }
  attachment(id: number): void {
    this.updates.push({
      update_id: id,
      message: {
        message_id: id,
        date: 1760000000,
        chat: { id: 1, type: "private" },
        from: {
          id: 1,
          is_bot: false,
          first_name: "Tester",
          language_code: "en",
        },
        caption: "attachment caption",
        document: {
          file_id: "file-1",
          file_unique_id: "unique-1",
          file_name: "sample.txt",
          file_size: 19,
        },
      },
    });
  }
  callback(id: number, data: string): void {
    this.updates.push({
      update_id: id,
      callback_query: {
        id: `callback-${String(id)}`,
        from: {
          id: 1,
          is_bot: false,
          first_name: "Tester",
          language_code: "en",
        },
        data,
        message: {
          message_id: 101,
          date: 1760000000,
          chat: { id: 1, type: "private" },
        },
        chat_instance: "test",
      },
    });
  }
  event(kind: string, turnId: string, extra: Partial<FakeEvent> = {}): void {
    this.events.push({
      kind,
      turnId,
      sessionId: "fake-session",
      cursor: String(this.events.length + 1),
      ...extra,
    });
  }
  async close(): Promise<void> {
    for (const response of this.held) response.destroy();
    this.server.closeAllConnections();
    await new Promise<void>((resolve, reject) =>
      this.server.close((error) => {
        if (error) reject(error);
        else resolve();
      }),
    );
  }
}
