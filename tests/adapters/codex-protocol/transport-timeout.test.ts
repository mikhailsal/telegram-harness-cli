import { createServer, type Socket } from "node:net";
import { mkdtemp, open, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, it, vi } from "vitest";
import { ProtocolTransport } from "../../../src/adapters/codex-protocol/transport.ts";

it("abandons a Unix socket that accepts but never completes the WebSocket handshake", async () => {
  const directory = await mkdtemp(join(tmpdir(), "thc-handshake-"));
  // Keep the socket in TMPDIR without exceeding Linux's Unix-socket path limit.
  const socketDirectory = await open(directory, "r");
  const path = `/proc/self/fd/${String(socketDirectory.fd)}/socket`;
  let client: Socket | undefined;
  let accepted: (() => void) | undefined;
  const connected = new Promise<void>((resolve) => {
    accepted = resolve;
  });
  const server = createServer((socket) => {
    client = socket;
    accepted?.();
  });
  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(path, () => {
      server.off("error", reject);
      resolve();
    });
  });
  vi.useFakeTimers();
  const transport = new ProtocolTransport(
    () => undefined,
    () => undefined,
  );
  try {
    const outcome = expect(transport.connect(path)).rejects.toThrow(
      "connection timed out",
    );
    await connected;
    await vi.advanceTimersByTimeAsync(30000);
    await outcome;
  } finally {
    transport.close();
    client?.destroy();
    vi.useRealTimers();
    await new Promise<void>((resolve, reject) =>
      server.close((error) => {
        if (error) reject(error);
        else resolve();
      }),
    );
    await socketDirectory.close();
    await rm(directory, { recursive: true, force: true });
  }
});
