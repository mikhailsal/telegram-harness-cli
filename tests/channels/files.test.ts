import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { basename, join } from "node:path";
import { expect, it } from "vitest";
import { openStore } from "../../src/store/index.js";
import {
  AttachmentManager,
  TelegramChannel,
} from "../../src/channels/index.js";

it("retries a download, keeps a stable safe path and removes unreferenced files", async () => {
  const directory = await mkdtemp(join(tmpdir(), "channel-files-"));
  const store = openStore(":memory:");
  recordDownloadInbox(store, directory);
  let downloads = 0;
  const fakeFetch: typeof fetch = retryDownloadFetcher(() => ++downloads);
  try {
    const channel = new TelegramChannel({
      token: "123:abc",
      allowedUserIds: [1],
      fetch: fakeFetch,
    });
    const manager = new AttachmentManager(
      channel,
      store,
      directory,
      "123:abc",
      "http://fake",
      fakeFetch,
    );
    const file = {
      kind: "document" as const,
      fileId: "f",
      fileUniqueId: "u",
      fileName: `../${"я".repeat(100)}`,
    };
    const path = await manager.download(
      1,
      file,
      new Date("2026-01-01T00:00:00Z"),
    );
    expect(Buffer.byteLength(basename(path))).toBeLessThanOrEqual(100);
    expect(await readFile(path, "utf8")).toBe("payload");
    expect(
      await manager.download(1, file, new Date("2026-01-02T00:00:00Z")),
    ).toBe(path);
    expect(downloads).toBe(3);
    await manager.retainSince(new Date("2026-02-01T00:00:00Z"));
    expect(store.listAttachments()).toHaveLength(1);
    consumeDownloadInbox(store);
    await manager.retainSince(new Date("2026-02-01T00:00:00Z"));
    expect(store.listAttachments()).toEqual([]);
  } finally {
    store.close();
    await rm(directory, { recursive: true, force: true });
  }
});

it("rejects oversized attachments before requesting the Bot API", async () => {
  const store = openStore(":memory:");
  let requests = 0;
  const fakeFetch: typeof fetch = () => {
    requests++;
    return Promise.reject(new Error("unexpected"));
  };
  const channel = new TelegramChannel({
    token: "123:abc",
    allowedUserIds: [1],
    fetch: fakeFetch,
  });
  const manager = new AttachmentManager(
    channel,
    store,
    ".",
    "123:abc",
    "http://fake",
    fakeFetch,
  );
  await expect(
    manager.download(
      1,
      {
        kind: "document",
        fileId: "f",
        fileUniqueId: "u",
        fileSize: 20 * 1024 * 1024 + 1,
      },
      new Date(),
    ),
  ).rejects.toThrow("attachment-too-large");
  expect(requests).toBe(0);
  store.close();
});

it("retries broken download streams three times and removes partial files", async () => {
  const directory = await mkdtemp(join(tmpdir(), "channel-failure-"));
  const store = openStore(":memory:");
  let downloads = 0;
  const fakeFetch: typeof fetch = brokenDownloadFetcher(() => ++downloads);
  try {
    const manager = new AttachmentManager(
      new TelegramChannel({
        token: "123:abc",
        allowedUserIds: [1],
        fetch: fakeFetch,
      }),
      store,
      directory,
      "123:abc",
      "http://fake",
      fakeFetch,
    );
    await expect(
      manager.download(
        1,
        { kind: "photo", fileId: "f", fileUniqueId: "u" },
        new Date("2026-01-01T00:00:00Z"),
      ),
    ).rejects.toThrow("interrupted");
    const { readdir } = await import("node:fs/promises");
    expect(await readdir(join(directory, "files", "2026-01-01"))).toEqual([]);
    expect(downloads).toBe(3);
  } finally {
    store.close();
    await rm(directory, { recursive: true, force: true });
  }
});

it("keeps a failed unlink tracked and removes it on a later retention pass", async () => {
  const { mkdir, writeFile } = await import("node:fs/promises");
  const directory = await mkdtemp(join(tmpdir(), "channel-retention-"));
  const store = openStore(":memory:");
  const path = join(directory, "cannot-unlink-directory");
  await mkdir(path);
  recordRetainedFile(store, directory, path);
  const manager = new AttachmentManager(
    new TelegramChannel({ token: "123:abc", allowedUserIds: [1] }),
    store,
    directory,
    "123:abc",
  );
  try {
    await manager.retainSince(new Date("2026-02-01T00:00:00Z"));
    expect(store.listAttachments()).toHaveLength(1);
    await rm(path, { recursive: true });
    await writeFile(path, "file");
    await manager.retainSince(new Date("2026-02-01T00:00:00Z"));
    expect(store.listAttachments()).toEqual([]);
  } finally {
    store.close();
    await rm(directory, { recursive: true, force: true });
  }
});

function retryDownloadFetcher(nextDownload: () => number): typeof fetch {
  return (input) => {
    const url =
      typeof input === "string"
        ? input
        : input instanceof URL
          ? input.href
          : input.url;
    if (url.includes("/getFile"))
      return Promise.resolve(
        Response.json({
          ok: true,
          result: { file_id: "f", file_unique_id: "u", file_path: "file.dat" },
        }),
      );
    const downloads = nextDownload();
    return Promise.resolve(
      downloads < 3
        ? new Response("error", { status: 500 })
        : new Response("payload"),
    );
  };
}

function brokenDownloadFetcher(nextDownload: () => number): typeof fetch {
  return (input) => {
    const url =
      typeof input === "string"
        ? input
        : input instanceof URL
          ? input.href
          : input.url;
    if (url.includes("/getFile"))
      return Promise.resolve(
        Response.json({
          ok: true,
          result: { file_id: "f", file_unique_id: "u", file_path: "f" },
        }),
      );
    nextDownload();
    return Promise.resolve(
      new Response(
        new ReadableStream({
          start(controller) {
            controller.enqueue(new TextEncoder().encode("partial"));
            controller.error(new Error("interrupted"));
          },
        }),
      ),
    );
  };
}

function recordRetainedFile(
  store: ReturnType<typeof openStore>,
  directory: string,
  path: string,
): void {
  store.putEntity({
    id: "e",
    directory,
    botId: "1",
    createdAt: "2026-01-01T00:00:00Z",
  });
  store.recordInbox({
    updateId: 1,
    entityId: "e",
    chatId: "1",
    userId: "1",
    receivedAt: "2026-01-01T00:00:00Z",
    payload: "{}",
  });
  store.createTurn({
    id: "t",
    entityId: "e",
    source: "telegram",
    createdAt: "2026-01-01T00:00:00Z",
  });
  store.assignInbox(1, "t");
  store.consumeInbox(1);
  store.putAttachment({
    updateId: 1,
    fileUniqueId: "u",
    fileKey: "key",
    path,
    createdAt: "2026-01-01T00:00:00Z",
    turnId: null,
  });
}

function recordDownloadInbox(
  store: ReturnType<typeof openStore>,
  directory: string,
): void {
  store.putEntity({
    id: "e",
    directory,
    botId: "1",
    createdAt: "2026-01-01T00:00:00Z",
  });
  store.recordInbox({
    updateId: 1,
    entityId: "e",
    chatId: "1",
    userId: "1",
    receivedAt: "2026-01-01T00:00:00Z",
    payload: "{}",
  });
}
function consumeDownloadInbox(store: ReturnType<typeof openStore>): void {
  store.createTurn({
    id: "t",
    entityId: "e",
    source: "telegram",
    createdAt: "2026-01-01T00:00:00Z",
  });
  store.assignInbox(1, "t");
  store.consumeInbox(1);
}
