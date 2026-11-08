import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, it, vi } from "vitest";
import { DownNotice } from "../../src/core/down-notice.ts";
it("persists intent before sending and does not send a duplicate after a crash", async () => {
  const root = await mkdtemp(join(tmpdir(), "thc-notice-"));
  const send = vi.fn().mockRejectedValueOnce(new Error("connection lost"));
  const api = { send, delete: vi.fn().mockResolvedValue(undefined) };
  try {
    const first = new DownNotice(root, api);
    await first.rememberLanguage("ru-RU");
    await expect(first.show("restart-1", "chat", "down")).rejects.toThrow(
      "connection lost",
    );
    const resumed = new DownNotice(root, api);
    expect(await resumed.language()).toBe("ru");
    await resumed.show("restart-1", "chat", "down");
    expect(send).toHaveBeenCalledTimes(1);
    await resumed.healthy();
    expect(await resumed.language()).toBe("ru");
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
it("records and deletes the sent notice, retaining state after failed deletion", async () => {
  const root = await mkdtemp(join(tmpdir(), "thc-notice-"));
  const api = {
    send: vi.fn().mockResolvedValue(42),
    delete: vi
      .fn()
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValue(undefined),
  };
  try {
    const notice = new DownNotice(root, api);
    await notice.rememberLanguage("en-US");
    await notice.show("one", "chat", "down");
    await expect(notice.healthy()).rejects.toThrow("offline");
    await notice.healthy();
    expect(api.delete).toHaveBeenCalledTimes(2);
    expect(await notice.language()).toBe("en");
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
it("removes a late notice after recovery during an in-flight send", async () => {
  const root = await mkdtemp(join(tmpdir(), "thc-notice-"));
  let finish: ((id: number) => void) | undefined;
  const api = {
    send: vi.fn(
      () =>
        new Promise<number>((resolve) => {
          finish = resolve;
        }),
    ),
    delete: vi.fn().mockResolvedValue(undefined),
  };
  try {
    const notice = new DownNotice(root, api);
    const pending = notice.show("old", "chat", "down");
    await vi.waitFor(() => {
      expect(finish).toBeDefined();
    });
    await notice.healthy();
    finish?.(17);
    await pending;
    expect(api.delete).toHaveBeenCalledWith("chat", 17);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
