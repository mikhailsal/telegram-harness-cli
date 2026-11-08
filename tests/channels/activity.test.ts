import { expect, it, vi } from "vitest";
import { logTelegramActivity } from "../../src/channels/activity.js";
import { TelegramChannel } from "../../src/channels/telegram.js";

const replyFetch: typeof fetch = (input) => {
  const url =
    typeof input === "string"
      ? input
      : input instanceof URL
        ? input.href
        : input.url;
  return Promise.resolve(
    Response.json({
      ok: true,
      result: url.endsWith("sendRichMessage") ? { message_id: 17 } : true,
    }),
  );
};

it("logs delivery and deletion IDs without message content or credentials", async () => {
  const log =
    vi.fn<(event: string, details: Record<string, unknown>) => void>();
  const channel = new TelegramChannel({
    token: "123:private-token",
    log,
    allowedUserIds: [1],
    fetch: replyFetch,
  });
  await channel.draft(1, 42, "private reasoning");
  await channel.send("1", "private reply");
  await channel.delete("1", 17);
  await channel.typing("1");
  expect(log.mock.calls.map((call) => call[1].method)).toEqual([
    "sendRichMessageDraft",
    "sendRichMessage",
    "deleteMessage",
  ]);
  expect(log).toHaveBeenCalledWith(
    "telegram.api",
    expect.objectContaining({
      method: "sendRichMessage",
      chat_id: "1",
      message_id: 17,
      ok: true,
    }),
  );
  expect(log.mock.calls[1]?.[1].startedAt).toEqual(expect.any(String));
  expect(log.mock.calls[1]?.[1].elapsedMs).toEqual(expect.any(Number));
  expect(JSON.stringify(log.mock.calls)).not.toMatch(/private|token/);
});

it("records definitive API rejection separately from an ambiguous transport failure", async () => {
  const log =
    vi.fn<(event: string, details: Record<string, unknown>) => void>();
  const fetcher = vi
    .fn<typeof fetch>()
    .mockResolvedValueOnce(
      Response.json({ ok: false, error_code: 429, description: "private" }),
    )
    .mockRejectedValueOnce(new Error("private URL with credentials"));
  const channel = new TelegramChannel({
    token: "123:private",
    allowedUserIds: [1],
    fetch: fetcher,
  });
  logTelegramActivity(channel, log);
  await expect(channel.delete("1", 17)).rejects.toThrow();
  await expect(channel.delete("1", 17)).rejects.toThrow();
  expect(log.mock.calls[0]?.[1]).toEqual(
    expect.objectContaining({ ok: false, code: 429 }),
  );
  expect(log.mock.calls[1]?.[1]).toEqual(
    expect.objectContaining({ ok: false, outcome: "unknown" }),
  );
  expect(JSON.stringify(log.mock.calls)).not.toMatch(/private|credentials/);
});
