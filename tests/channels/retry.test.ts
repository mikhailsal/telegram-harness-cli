import { expect, it } from "vitest";
import { openStore } from "../../src/store/index.js";
import {
  TelegramChannel,
  TransientManager,
  transientIntent,
} from "../../src/channels/index.js";

it("retries a definitively rejected transient only after persisted retry_after", async () => {
  const store = openStore(":memory:");
  let now = new Date("2026-01-01T00:00:00Z"),
    attempts = 0;
  const fakeFetch: typeof fetch = () => {
    attempts++;
    return Promise.resolve(
      attempts === 1
        ? Response.json({
            ok: false,
            error_code: 429,
            description: "rate limited",
            parameters: { retry_after: 5 },
          })
        : Response.json({
            ok: true,
            result: {
              message_id: 7,
              date: 1,
              chat: { id: 1, type: "private" },
            },
          }),
    );
  };
  const channel = new TelegramChannel({
    token: "123:abc",
    allowedUserIds: [1],
    fetch: fakeFetch,
  });
  const intent = transientIntent("card", "approval", "1", now);
  const first = new TransientManager(channel, store, () => now);
  await expect(first.send(intent, "card")).rejects.toThrow();
  expect(store.getCursor("transient-retry/card")).toBe(
    "2026-01-01T00:00:05.000Z",
  );
  const restarted = new TransientManager(channel, store, () => now);
  expect(await restarted.send(intent, "card")).toBeUndefined();
  expect(attempts).toBe(1);
  now = new Date("2026-01-01T00:00:05Z");
  expect(await restarted.send(intent, "card")).toBe(7);
  expect(attempts).toBe(2);
  store.close();
});
it("never repeats an ambiguous transient send after restart", async () => {
  const store = openStore(":memory:");
  let attempts = 0;
  const fetcher: typeof fetch = () => {
    attempts++;
    return Promise.reject(new Error("connection lost"));
  };
  const channel = new TelegramChannel({
    token: "123:abc",
    allowedUserIds: [1],
    fetch: fetcher,
  });
  const intent = transientIntent(
    "card",
    "approval",
    "1",
    new Date("2026-01-01T00:00:00Z"),
  );
  await expect(
    new TransientManager(channel, store).send(intent, "card"),
  ).rejects.toThrow();
  expect(
    await new TransientManager(channel, store).send(intent, "card"),
  ).toBeUndefined();
  expect(attempts).toBe(1);
  store.close();
});
it("persists a repost intent and suppresses retry after an ambiguous replacement send", async () => {
  const store = openStore(":memory:");
  const now = new Date("2026-01-02T23:00:00Z");
  store.putTransient(
    transientIntent(
      "approval",
      "approval",
      "1",
      new Date("2026-01-01T00:00:00Z"),
    ),
  );
  store.setTransientMessage("approval", 7, "2026-01-01T00:00:00.000Z");
  const channel = new TelegramChannel({
    token: "123:abc",
    allowedUserIds: [1],
  });
  let attempts = 0;
  const send = () => {
    attempts++;
    return Promise.reject(new Error("ambiguous"));
  };
  await expect(
    new TransientManager(channel, store, () => now).repost(
      "approval",
      now,
      send,
    ),
  ).rejects.toThrow();
  expect(
    store.listTransients().find((row) => row.id === "approval/repost/7")
      ?.messageId,
  ).toBeNull();
  expect(
    await new TransientManager(channel, store, () => now).repost(
      "approval",
      now,
      send,
    ),
  ).toBe(false);
  expect(attempts).toBe(1);
  expect(
    store.listTransients().find((row) => row.id === "approval")?.messageId,
  ).toBe(7);
  store.close();
});

it("retries a rejected repost only after its persisted deadline", async () => {
  const store = openStore(":memory:");
  let now = new Date("2026-01-02T23:00:00Z"),
    attempts = 0;
  store.putTransient(
    transientIntent(
      "approval",
      "approval",
      "1",
      new Date("2026-01-01T00:00:00Z"),
    ),
  );
  store.setTransientMessage("approval", 7, "2026-01-01T00:00:00.000Z");
  const fetcher: typeof fetch = rejectedRepostFetcher(() => ++attempts);
  const channel = new TelegramChannel({
    token: "123:abc",
    allowedUserIds: [1],
    fetch: fetcher,
  });
  const send = async () =>
    (await channel.bot.api.sendMessage(1, "card")).message_id;
  await expect(
    new TransientManager(channel, store, () => now).repost(
      "approval",
      now,
      send,
    ),
  ).rejects.toThrow();
  expect(
    await new TransientManager(channel, store, () => now).repost(
      "approval",
      now,
      send,
    ),
  ).toBe(false);
  now = new Date("2026-01-02T23:00:03Z");
  expect(
    await new TransientManager(channel, store, () => now).repost(
      "approval",
      now,
      send,
    ),
  ).toBe(true);
  expect(attempts).toBe(2);
  expect(store.listTransients()).toMatchObject([
    { id: "approval", messageId: 8 },
  ]);
  store.close();
});
it("reconciles a replacement saved before the original card was updated", async () => {
  const store = openStore(":memory:");
  const now = new Date("2026-01-02T23:00:00Z");
  store.putTransient(
    transientIntent(
      "approval",
      "approval",
      "1",
      new Date("2026-01-01T00:00:00Z"),
    ),
  );
  store.setTransientMessage("approval", 7, "2026-01-01T00:00:00.000Z");
  store.putTransient(
    transientIntent("approval/repost/7", "approval", "1", now),
  );
  store.setTransientMessage("approval/repost/7", 8, now.toISOString());
  const fetcher: typeof fetch = () =>
    Promise.resolve(Response.json({ ok: true, result: true }));
  const channel = new TelegramChannel({
    token: "123:abc",
    allowedUserIds: [1],
    fetch: fetcher,
  });
  let resends = 0;
  const result = await new TransientManager(channel, store, () => now).repost(
    "approval",
    now,
    () => {
      resends++;
      return Promise.resolve(9);
    },
  );
  expect(result).toBe(true);
  expect(resends).toBe(0);
  expect(store.listTransients()).toMatchObject([
    { id: "approval", messageId: 8 },
  ]);
  store.close();
});

it("cleans the old card after a restart following replacement commit", async () => {
  const store = openStore(":memory:");
  const now = new Date("2026-01-02T23:00:00Z");
  store.putTransient(
    transientIntent(
      "approval",
      "approval",
      "1",
      new Date("2026-01-01T00:00:00Z"),
    ),
  );
  store.setTransientMessage("approval", 7, "2026-01-01T00:00:00.000Z");
  store.putTransient(
    transientIntent("approval/repost/7", "approval", "1", now),
  );
  store.setTransientMessage("approval/repost/7", 8, now.toISOString());
  store.replaceTransientMessage("approval", 7, 8, now.toISOString(), null);
  const methods: string[] = [];
  const fetcher: typeof fetch = (input) => {
    const url =
      typeof input === "string"
        ? input
        : input instanceof URL
          ? input.href
          : input.url;
    methods.push(url.split("/").at(-1) ?? "");
    return Promise.resolve(Response.json({ ok: true, result: true }));
  };
  const channel = new TelegramChannel({
    token: "123:abc",
    allowedUserIds: [1],
    fetch: fetcher,
  });
  expect(
    await new TransientManager(channel, store, () => now).repost(
      "approval",
      now,
      () => Promise.resolve(9),
    ),
  ).toBe(false);
  expect(methods).toEqual(["deleteMessage"]);
  expect(store.listTransients()).toMatchObject([
    { id: "approval", messageId: 8 },
  ]);
  store.close();
});
it("removes a newly accepted message when its intent was cancelled in flight", async () => {
  const store = openStore(":memory:");
  let finish: ((id: number) => void) | undefined;
  const methods: string[] = [];
  const fetcher: typeof fetch = (input) => {
    const url =
      typeof input === "string"
        ? input
        : input instanceof URL
          ? input.href
          : input.url;
    methods.push(url.split("/").at(-1) ?? "");
    return Promise.resolve(Response.json({ ok: true, result: true }));
  };
  const channel = new TelegramChannel({
    token: "123:abc",
    allowedUserIds: [1],
    fetch: fetcher,
  });
  const manager = new TransientManager(channel, store);
  const intent = transientIntent("card", "approval", "1", new Date());
  const pending = manager.create(
    intent,
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  await manager.remove("card");
  finish?.(9);
  expect(await pending).toBeUndefined();
  expect(methods).toEqual(["deleteMessage"]);
  expect(store.listTransients()).toEqual([]);
  store.close();
});

function rejectedRepostFetcher(nextAttempt: () => number): typeof fetch {
  return (input) => {
    const url =
      typeof input === "string"
        ? input
        : input instanceof URL
          ? input.href
          : input.url;
    if (url.endsWith("sendMessage")) {
      const attempts = nextAttempt();
      return Promise.resolve(
        attempts === 1
          ? Response.json({
              ok: false,
              error_code: 429,
              description: "wait",
              parameters: { retry_after: 3 },
            })
          : Response.json({
              ok: true,
              result: {
                message_id: 8,
                date: 1,
                chat: { id: 1, type: "private" },
              },
            }),
      );
    }
    return Promise.resolve(Response.json({ ok: true, result: true }));
  };
}
