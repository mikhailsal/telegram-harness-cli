import { afterEach, expect, it, vi } from "vitest";
import { ChannelStatus, TelegramChannel } from "../../src/channels/index.js";

afterEach(() => vi.useRealTimers());
it("coalesces status updates and observes Telegram retry_after", async () => {
  vi.useFakeTimers();
  const calls: string[] = [];
  const fakeFetch: typeof fetch = (_input, init) => {
    const body = JSON.parse(
      typeof init?.body === "string" ? init.body : "{}",
    ) as { rich_message?: { blocks?: { text?: string }[] } };
    calls.push(body.rich_message?.blocks?.[0]?.text ?? "");
    if (calls.length === 1)
      return Promise.resolve(
        Response.json({
          ok: false,
          error_code: 429,
          description: "wait",
          parameters: { retry_after: 3 },
        }),
      );
    return Promise.resolve(Response.json({ ok: true, result: true }));
  };
  const channel = new TelegramChannel({
    token: "123:abc",
    allowedUserIds: [1],
    fetch: fakeFetch,
  });
  const status = new ChannelStatus(channel, 1, 42);
  status.update("first");
  status.update("latest");
  await vi.advanceTimersByTimeAsync(1);
  expect(calls).toEqual(["latest"]);
  await vi.advanceTimersByTimeAsync(2998);
  expect(calls).toEqual(["latest"]);
  await vi.advanceTimersByTimeAsync(1);
  expect(calls).toEqual(["latest", "latest"]);
  await status.stop();
});

function fallbackTransport(methods: string[]): typeof fetch {
  return (input) => {
    const url =
      typeof input === "string"
        ? input
        : input instanceof URL
          ? input.href
          : input.url;
    const method = url.split("/").at(-1) ?? "";
    methods.push(method);
    if (method === "sendRichMessageDraft")
      return Promise.resolve(
        Response.json({
          ok: false,
          error_code: 400,
          description: "unsupported",
        }),
      );
    const result =
      method === "sendMessage"
        ? { message_id: 10, date: 1, chat: { id: 1, type: "private" } }
        : true;
    return Promise.resolve(Response.json({ ok: true, result }));
  };
}

it("falls back to a durable editable message and removes it when stopped", async () => {
  vi.useFakeTimers();
  const methods: string[] = [];
  const fakeFetch = fallbackTransport(methods);
  const { openStore } = await import("../../src/store/index.js");
  const { TransientManager } = await import("../../src/channels/index.js");
  const store = openStore(":memory:");
  const channel = new TelegramChannel({
    token: "123:abc",
    allowedUserIds: [1],
    fetch: fakeFetch,
  });
  const status = new ChannelStatus(
    channel,
    1,
    42,
    new TransientManager(channel, store),
    "status",
  );
  status.update("start");
  await vi.advanceTimersByTimeAsync(1);
  expect(store.listTransients()[0]?.messageId).toBe(10);
  status.update("changed");
  await vi.advanceTimersByTimeAsync(1000);
  await status.stop();
  expect(methods).toEqual([
    "sendRichMessageDraft",
    "sendMessage",
    "sendChatAction",
    "editMessageText",
    "sendChatAction",
    "deleteMessage",
  ]);
  expect(store.listTransients()).toEqual([]);
  store.close();
});

it("delivers an update queued during an in-flight draft and waits on stop", async () => {
  vi.useFakeTimers();
  const texts: string[] = [];
  let resolveFirst: ((value: Response) => void) | undefined;
  const fakeFetch: typeof fetch = (_input, init) => {
    const body = JSON.parse(
      typeof init?.body === "string" ? init.body : "{}",
    ) as { rich_message?: { blocks?: { text?: string }[] } };
    texts.push(body.rich_message?.blocks?.[0]?.text ?? "");
    if (texts.length === 1)
      return new Promise<Response>((resolve) => {
        resolveFirst = resolve;
      });
    return Promise.resolve(Response.json({ ok: true, result: true }));
  };
  const channel = new TelegramChannel({
    token: "123:abc",
    allowedUserIds: [1],
    fetch: fakeFetch,
  });
  const status = new ChannelStatus(channel, 1, 42);
  status.update("first");
  await vi.advanceTimersByTimeAsync(1);
  status.update("second");
  await vi.advanceTimersByTimeAsync(1000);
  expect(texts).toEqual(["first"]);
  resolveFirst?.(Response.json({ ok: true, result: true }));
  await vi.advanceTimersByTimeAsync(1000);
  expect(texts).toEqual(["first", "second"]);
  await status.stop();
});

it("deletes a fallback message even when stop races its send", async () => {
  vi.useFakeTimers();
  const methods: string[] = [];
  let completeSend: ((value: Response) => void) | undefined;
  const fakeFetch: typeof fetch = (input) => {
    const url =
      typeof input === "string"
        ? input
        : input instanceof URL
          ? input.href
          : input.url;
    const method = url.split("/").at(-1) ?? "";
    methods.push(method);
    if (method === "sendRichMessageDraft")
      return Promise.resolve(
        Response.json({
          ok: false,
          error_code: 400,
          description: "unsupported",
        }),
      );
    if (method === "sendMessage")
      return new Promise<Response>((resolve) => {
        completeSend = resolve;
      });
    return Promise.resolve(Response.json({ ok: true, result: true }));
  };
  const channel = new TelegramChannel({
    token: "123:abc",
    allowedUserIds: [1],
    fetch: fakeFetch,
  });
  const status = new ChannelStatus(channel, 1, 42);
  status.update("working");
  await vi.advanceTimersByTimeAsync(1);
  const stopped = status.stop();
  completeSend?.(
    Response.json({
      ok: true,
      result: { message_id: 9, date: 1, chat: { id: 1, type: "private" } },
    }),
  );
  await stopped;
  expect(methods).toContain("deleteMessage");
});

it("uses editable messages directly in message mode", async () => {
  vi.useFakeTimers();
  const methods: string[] = [];
  const fakeFetch: typeof fetch = (input) => {
    const url =
      typeof input === "string"
        ? input
        : input instanceof URL
          ? input.href
          : input.url;
    const method = url.split("/").at(-1) ?? "";
    methods.push(method);
    return Promise.resolve(
      Response.json({
        ok: true,
        result:
          method === "sendMessage"
            ? { message_id: 7, date: 1, chat: { id: 1, type: "private" } }
            : true,
      }),
    );
  };
  const status = new ChannelStatus(
    new TelegramChannel({
      token: "123:abc",
      allowedUserIds: [1],
      fetch: fakeFetch,
    }),
    1,
    2,
    undefined,
    undefined,
    "message",
  );
  status.update("work");
  await vi.advanceTimersByTimeAsync(1);
  await status.stop();
  expect(methods).toEqual(["sendMessage", "sendChatAction", "deleteMessage"]);
});

it("uses the injected clock for durable fallback status timestamps", async () => {
  vi.useFakeTimers();
  const { openStore } = await import("../../src/store/index.js");
  const { TransientManager } = await import("../../src/channels/index.js");
  const store = openStore(":memory:");
  const now = () => new Date("2026-01-01T00:00:00Z");
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
        result: url.endsWith("sendMessage")
          ? { message_id: 1, date: 1, chat: { id: 1, type: "private" } }
          : true,
      }),
    );
  };
  const channel = new TelegramChannel({
    token: "123:abc",
    allowedUserIds: [1],
    fetch: fetcher,
  });
  const status = new ChannelStatus(
    channel,
    1,
    2,
    new TransientManager(channel, store, now),
    "status",
    "message",
    now,
  );
  status.update("work");
  await vi.advanceTimersByTimeAsync(1);
  expect(store.listTransients()[0]).toMatchObject({
    createdAt: "2026-01-01T00:00:00.000Z",
    expiresAt: null,
  });
  await status.stop();
  store.close();
});

function repostTransport() {
  let next = 0;
  const methods: string[] = [];
  const fetcher: typeof fetch = (input) => {
    const url =
      typeof input === "string"
        ? input
        : input instanceof URL
          ? input.href
          : input.url;
    const method = url.split("/").at(-1) ?? "";
    methods.push(method);
    return Promise.resolve(
      Response.json({
        ok: true,
        result:
          method === "sendMessage"
            ? { message_id: ++next, date: 1, chat: { id: 1, type: "private" } }
            : true,
      }),
    );
  };
  return { fetcher, methods, count: () => next };
}

it("reposts a long-running fallback before the Telegram deletion window", async () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-01-01T00:00:00Z"));
  const { openStore } = await import("../../src/store/index.js");
  const { TransientManager } = await import("../../src/channels/index.js");
  const store = openStore(":memory:");
  const { fetcher, methods, count } = repostTransport();
  const channel = new TelegramChannel({
    token: "123:abc",
    allowedUserIds: [1],
    fetch: fetcher,
  });
  const edit = vi.spyOn(channel, "edit");
  const now = () => new Date();
  const status = new ChannelStatus(
    channel,
    1,
    2,
    new TransientManager(channel, store, now),
    "status",
    "message",
    now,
  );
  try {
    status.update("work");
    await vi.advanceTimersByTimeAsync(1);
    vi.setSystemTime(new Date("2026-01-03T00:00:00Z"));
    status.update("still working");
    await vi.advanceTimersByTimeAsync(1000);
    expect(count()).toBe(2);
    expect(store.listTransients()[0]?.messageId).toBe(2);
    expect(methods).toContain("deleteMessage");
    expect(edit).toHaveBeenLastCalledWith("1", 2, "still working");
  } finally {
    await status.stop();
    store.close();
  }
});

it("renews an aged recovered status on its first update", async () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-01-03T00:00:00Z"));
  const { openStore } = await import("../../src/store/index.js");
  const { TransientManager } = await import("../../src/channels/index.js");
  const store = openStore(":memory:");
  const old = "2026-01-01T00:00:00.000Z";
  store.putTransient({
    id: "status",
    kind: "status",
    chatId: "1",
    createdAt: old,
    expiresAt: null,
  });
  store.setTransientMessage("status", 99, old);
  const { fetcher, methods, count } = repostTransport();
  const channel = new TelegramChannel({
    token: "123:abc",
    allowedUserIds: [1],
    fetch: fetcher,
  });
  const edit = vi.spyOn(channel, "edit");
  const now = () => new Date();
  const status = new ChannelStatus(
    channel,
    1,
    2,
    new TransientManager(channel, store, now),
    "status",
    "message",
    now,
  );
  try {
    status.update("recovered");
    await vi.advanceTimersByTimeAsync(1);
    expect(count()).toBe(1);
    expect(store.listTransients()[0]?.messageId).toBe(1);
    expect(methods).toContain("deleteMessage");
    expect(edit).toHaveBeenCalledWith("1", 1, "recovered");
  } finally {
    await status.stop();
    store.close();
  }
});
