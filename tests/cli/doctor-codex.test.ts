import { createHash } from "node:crypto";
import { createServer } from "node:http";
import type { IncomingMessage, Server } from "node:http";
import { mkdtemp, open, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { Duplex } from "node:stream";
import { WebSocketServer } from "ws";
import { afterEach, expect, it } from "vitest";
import { SettingsUnsupportedError } from "../../src/adapters/contract.js";
import type { CliDependencies } from "../../src/cli/context.js";
import { askModelList, modelListCheck } from "../../src/cli/doctor-codex.js";
import { parseConfig } from "../../src/core/config.js";

type Mode = "answer" | "unsupported" | "silent" | "wedged";
const cleanups: (() => Promise<void>)[] = [];
afterEach(async () => {
  for (const cleanup of cleanups.splice(0)) await cleanup();
});

// Answers initialize and then model/list as the mode says, or nothing at all
// when silent. A silent daemon still answers the closing handshake.
function answer(server: Server, mode: Mode, seen: string[]): () => void {
  const ws = new WebSocketServer({ server });
  ws.on("connection", (client) => {
    client.on("message", (data) => {
      const request = JSON.parse(Buffer.from(data as Buffer).toString()) as {
        id?: string;
        method: string;
      };
      seen.push(request.method);
      if (mode === "silent" || request.id === undefined) return;
      const reply =
        request.method === "model/list" && mode === "unsupported"
          ? { error: { code: -32601, message: "method not found" } }
          : {
              result:
                request.method === "model/list"
                  ? { data: [], nextCursor: null }
                  : {},
            };
      client.send(JSON.stringify({ id: request.id, ...reply }));
    });
  });
  return () => {
    for (const client of ws.clients) client.terminate();
  };
}

// A wedged daemon accepts the WebSocket upgrade and then reads nothing, not
// even the close frame.
function wedge(server: Server): () => void {
  const connections: Duplex[] = [];
  server.on("upgrade", (request: IncomingMessage, connection: Duplex) => {
    const accept = createHash("sha1")
      .update(
        `${String(request.headers["sec-websocket-key"])}258EAFA5-E914-47DA-95CA-C5AB0DC85B11`,
      )
      .digest("base64");
    connection.write(
      `HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: ${accept}\r\n\r\n`,
    );
    connection.pause();
    connections.push(connection);
  });
  return () => {
    for (const connection of connections) connection.destroy();
  };
}

// A Codex daemon stand-in on a Unix socket.
async function daemon(mode: Mode): Promise<{ socket: string; seen: string[] }> {
  const directory = await mkdtemp(join(tmpdir(), "thc-doctor-"));
  // A Unix socket path is limited to 108 bytes and the gate's temporary
  // folder is longer, so the socket is bound through a descriptor's short path.
  const handle = await open(directory, "r");
  const socket = `/proc/self/fd/${String(handle.fd)}/control.sock`;
  const server = createServer();
  const seen: string[] = [];
  const drop = mode === "wedged" ? wedge(server) : answer(server, mode, seen);
  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(socket, resolve);
  });
  cleanups.push(async () => {
    drop();
    await new Promise<void>((resolve) =>
      server.close(() => {
        resolve();
      }),
    );
    await handle.close();
    await rm(directory, { recursive: true, force: true });
  });
  return { socket, seen };
}
function config(codex: Record<string, unknown> = {}) {
  return parseConfig({
    token: { env: "TOKEN" },
    allowlist: [1],
    sandbox: "read-only",
    codex,
  });
}
const deps = (hook?: CliDependencies["listCodexModels"]) =>
  ({ ...(hook ? { listCodexModels: hook } : {}) }) as CliDependencies;

it("asks the daemon for model/list after the handshake and closes", async () => {
  const fake = await daemon("answer");
  await askModelList(fake.socket, AbortSignal.timeout(5000));
  expect(fake.seen).toEqual(["initialize", "initialized", "model/list"]);
  expect(await modelListCheck(config({ socket: fake.socket }), deps())).toBe(
    "supported",
  );
});

it("tells an old Codex without model/list from a missing daemon", async () => {
  const fake = await daemon("unsupported");
  await expect(
    askModelList(fake.socket, AbortSignal.timeout(5000)),
  ).rejects.toBeInstanceOf(SettingsUnsupportedError);
  expect(await modelListCheck(config({ socket: fake.socket }), deps())).toBe(
    "unsupported: /model needs codex-cli 0.169.0 or newer",
  );
  const missing = fake.socket.replace("control.sock", "missing.sock");
  expect(await modelListCheck(config({ socket: missing }), deps())).toBe(
    "unavailable: the Codex daemon is not running",
  );
});

// Value: protects=doctor gives up within its budget on a daemon that accepts the connection and then hangs; fails_when=the abort waits for a closing handshake a wedged daemon never answers (30 s); why_new=the silent stand-in still answers the close frame, so a wedged daemon was untested; seam=none; no_claim=a real wedged Codex process
it("gives up on a daemon that never answers when the budget ends", async () => {
  for (const mode of ["silent", "wedged"] as const) {
    const fake = await daemon(mode);
    const started = Date.now();
    await expect(
      askModelList(fake.socket, AbortSignal.timeout(200)),
    ).rejects.toThrow();
    expect(Date.now() - started).toBeLessThan(5000);
  }
});

it("skips the tmux adapter and reports other failures plainly", async () => {
  let asked = 0;
  const hook = () => {
    asked++;
    return Promise.reject(new Error("socket hang up"));
  };
  expect(await modelListCheck(config({ adapter: "tmux" }), deps(hook))).toBe(
    "not checked: the tmux adapter does not switch models",
  );
  expect(asked).toBe(0);
  expect(await modelListCheck(config(), deps(hook))).toBe("failed");
  expect(asked).toBe(1);
});
