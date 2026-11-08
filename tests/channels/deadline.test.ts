import { afterEach, expect, it, vi } from "vitest";
import { TelegramChannel } from "../../src/channels/index.js";

afterEach(() => vi.restoreAllMocks());
it("returns allowed updates and the batch offset when a stranger reply is rejected", async () => {
  let replyAttempts = 0;
  const fetcher: typeof fetch = (input) => {
    const url =
      typeof input === "string"
        ? input
        : input instanceof URL
          ? input.href
          : input.url;
    if (url.endsWith("getUpdates"))
      return Promise.resolve(
        Response.json({
          ok: true,
          result: strangerUpdates(),
        }),
      );
    replyAttempts++;
    return Promise.resolve(
      Response.json({ ok: false, error_code: 403, description: "blocked" }),
    );
  };
  const channel = new TelegramChannel({
    token: "123:abc",
    allowedUserIds: [1],
    fetch: fetcher,
  });
  const result = await channel.poll(0);
  expect(result.nextOffset).toBe(6);
  expect(result.events).toHaveLength(1);
  expect(result.events[0]).toMatchObject({ kind: "message", text: "task" });
  expect(replyAttempts).toBe(1);
});
it("uses 55-second poll and 30-second direct API deadlines", async () => {
  const timeout = vi
    .spyOn(AbortSignal, "timeout")
    .mockImplementation(() => new AbortController().signal);
  const fetcher: typeof fetch = (input) => {
    const url =
      typeof input === "string"
        ? input
        : input instanceof URL
          ? input.href
          : input.url;
    return Promise.resolve(
      Response.json({
        ok: true,
        result: url.endsWith("getUpdates") ? [] : true,
      }),
    );
  };
  const channel = new TelegramChannel({
    token: "123:abc",
    allowedUserIds: [1],
    fetch: fetcher,
  });
  await channel.poll(0);
  await channel.typing("1");
  await channel.draft(1, 2, "work");
  expect(timeout.mock.calls.map(([ms]) => ms)).toEqual([55000, 30000, 30000]);
});
it("cancels direct SDK calls when the channel shutdown signal aborts", async () => {
  const controller = new AbortController();
  let started = false;
  const fetcher: typeof fetch = (_input, init) =>
    new Promise((_resolve, reject) => {
      started = true;
      init?.signal?.addEventListener(
        "abort",
        () => {
          reject(new Error("aborted"));
        },
        { once: true },
      );
    });
  const channel = new TelegramChannel({
    token: "123:abc",
    allowedUserIds: [1],
    fetch: fetcher,
    signal: controller.signal,
  });
  const pending = channel.bot.api.sendMessage(1, "text");
  await vi.waitFor(() => {
    expect(started).toBe(true);
  });
  controller.abort();
  await expect(pending).rejects.toThrow();
});

it("cancels an in-progress attachment download during shutdown", async () => {
  const { openStore } = await import("../../src/store/index.js");
  const { AttachmentManager } = await import("../../src/channels/index.js");
  const { mkdtemp, rm } = await import("node:fs/promises");
  const root = await mkdtemp("/tmp/thc-cancel-download-");
  const store = openStore(":memory:");
  const controller = new AbortController();
  let started = false;
  const fetcher: typeof fetch = cancellationFetcher(() => {
    started = true;
  });
  try {
    const channel = new TelegramChannel({
      token: "123:abc",
      allowedUserIds: [1],
      fetch: fetcher,
      signal: controller.signal,
    });
    const manager = new AttachmentManager(
      channel,
      store,
      root,
      "123:abc",
      "http://fake",
      fetcher,
    );
    const pending = manager.download(
      1,
      { kind: "document", fileId: "f", fileUniqueId: "u" },
      new Date(),
    );
    await vi.waitFor(() => {
      expect(started).toBe(true);
    });
    controller.abort();
    await expect(pending).rejects.toThrow();
  } finally {
    store.close();
    await rm(root, { recursive: true, force: true });
  }
});

function strangerUpdates() {
  return [
    {
      update_id: 4,
      message: {
        message_id: 4,
        date: 1,
        chat: { id: 2, type: "private" },
        from: { id: 2, is_bot: false, first_name: "stranger" },
        text: "hi",
      },
    },
    {
      update_id: 5,
      message: {
        message_id: 5,
        date: 1,
        chat: { id: 1, type: "private" },
        from: { id: 1, is_bot: false, first_name: "owner" },
        text: "task",
      },
    },
  ];
}

function cancellationFetcher(markStarted: () => void): typeof fetch {
  return (input, init) => {
    const url =
      typeof input === "string"
        ? input
        : input instanceof URL
          ? input.href
          : input.url;
    if (url.endsWith("getFile"))
      return Promise.resolve(
        Response.json({
          ok: true,
          result: { file_id: "f", file_unique_id: "u", file_path: "file" },
        }),
      );
    return new Promise((_resolve, reject) => {
      markStarted();
      if (init?.signal?.aborted) {
        reject(new Error("aborted"));
        return;
      }
      init?.signal?.addEventListener(
        "abort",
        () => {
          reject(new Error("aborted"));
        },
        { once: true },
      );
    });
  };
}
