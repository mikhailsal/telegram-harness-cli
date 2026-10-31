import { describe, expect, it } from "vitest";
import {
  TelegramChannel,
  entitiesToMarkdown,
  renderOutgoing,
  safeFileName,
  richBlocksToMarkdown,
} from "../../src/channels/index.js";

function fakeApi(updates: unknown[] = [], fail: string[] = []) {
  const calls: { method: string; body: Record<string, unknown> }[] = [];
  const fetcher: typeof fetch = (input, init) => {
    const method =
      (typeof input === "string"
        ? input
        : input instanceof URL
          ? input.href
          : input.url
      )
        .split("/")
        .at(-1) ?? "";
    const body =
      typeof init?.body === "string"
        ? (JSON.parse(init.body) as Record<string, unknown>)
        : {};
    calls.push({ method, body });
    if (fail.includes(method))
      return Promise.resolve(
        Response.json({
          ok: false,
          error_code: 400,
          description: "bad request",
        }),
      );
    const result =
      method === "getUpdates"
        ? updates
        : {
            message_id: calls.length,
            date: 1,
            chat: { id: 1, type: "private" },
          };
    return Promise.resolve(Response.json({ ok: true, result }));
  };
  return { calls, fetcher };
}
describe("Telegram channel", () => {
  it("filters strangers and groups while advancing over every update", async () => {
    const api = fakeApi([
      {
        update_id: 1,
        message: {
          message_id: 1,
          date: 1,
          chat: { id: 3, type: "group" },
          from: { id: 3, is_bot: false, first_name: "x" },
          text: "hidden",
        },
      },
      {
        update_id: 2,
        message: {
          message_id: 2,
          date: 1,
          chat: { id: 4, type: "private" },
          from: { id: 4, is_bot: false, first_name: "x" },
          text: "hello",
        },
      },
      {
        update_id: 3,
        message: {
          message_id: 3,
          date: 1,
          chat: { id: 1, type: "private" },
          from: { id: 1, is_bot: false, first_name: "x" },
          text: "hello",
        },
      },
    ]);
    const channel = new TelegramChannel({
      token: "123:abc",
      allowedUserIds: [1],
      fetch: api.fetcher,
    });
    const result = await channel.poll(0);
    expect(result.nextOffset).toBe(4);
    expect(result.events).toHaveLength(1);
    expect(
      api.calls
        .filter((call) => call.method === "sendMessage")
        .map((call) => call.body.text),
    ).toEqual(["4"]);
  });
});
describe("Telegram channel", () => {
  it("falls back from rich to plain without leaking hidden content", async () => {
    const api = fakeApi([], ["sendRichMessage"]);
    const channel = new TelegramChannel({
      token: "123:abc",
      allowedUserIds: [1],
      fetch: api.fetcher,
    });
    const result = await channel.send(
      "1",
      "Hello <internal-note>secret</internal-note> world",
    );
    expect(result.visibleText).toBe("Hello  world");
    expect(api.calls.map((call) => call.method)).toEqual([
      "sendRichMessage",
      "sendMessage",
    ]);
    expect(JSON.stringify(api.calls)).not.toContain("secret");
  });
});
describe("Telegram channel", () => {
  it("hides unclosed tags but preserves code literals", () => {
    expect(renderOutgoing("ok <internal-note>secret").visibleText).toBe("ok");
    expect(
      renderOutgoing("`<internal-note>visible</internal-note>`").visibleText,
    ).toContain("visible");
    expect(
      renderOutgoing("```\n<internal-note>visible</internal-note>\n```")
        .visibleText,
    ).toContain("visible");
  });
});
describe("Telegram channel", () => {
  it("splits at blocks and moves oversized code to a file", () => {
    const result = renderOutgoing(
      `before\n\n\`\`\`\n${"a".repeat(33000)}\n\`\`\`\n\nafter`,
    );
    expect(result.chunks.map((chunk) => chunk.kind)).toEqual([
      "message",
      "file",
      "message",
    ]);
  });
});
describe("Telegram channel", () => {
  it("converts message entities and sanitizes filenames", () => {
    expect(
      entitiesToMarkdown("hello", [{ type: "bold", offset: 0, length: 5 }]),
    ).toBe("**hello**");
    const name = safeFileName(`../${"я".repeat(100)}\u0000`);
    expect(Buffer.byteLength(name)).toBeLessThanOrEqual(100);
    expect(name).not.toContain("/");
  });
});

it("converts rich incoming text, headings, lists, and preformatted blocks", () => {
  expect(
    richBlocksToMarkdown([
      { type: "heading", size: 2, text: "Title" },
      { type: "paragraph", text: [{ type: "bold", text: "bold" }, " text"] },
      {
        type: "list",
        items: [{ label: "•", blocks: [{ type: "paragraph", text: "item" }] }],
      },
      { type: "pre", text: "code", language: "ts" },
    ]),
  ).toBe("## Title\n\n**bold** text\n\n- item\n\n```ts\ncode\n```");
});

describe("delivery boundaries", () => {
  it("sends a document when rich and plain are rejected", async () => {
    const api = fakeApi([], ["sendRichMessage", "sendMessage"]);
    const channel = new TelegramChannel({
      token: "123:abc",
      allowedUserIds: [1],
      fetch: api.fetcher,
    });
    const result = await channel.send("1", "**answer**");
    expect(api.calls.map((call) => call.method)).toEqual([
      "sendRichMessage",
      "sendMessage",
      "sendDocument",
    ]);
    expect(result.visibleText).toBe("**answer**");
  });
  it("skips plain text for an oversized fallback", async () => {
    const api = fakeApi([], ["sendRichMessage"]);
    const channel = new TelegramChannel({
      token: "123:abc",
      allowedUserIds: [1],
      fetch: api.fetcher,
    });
    await channel.send("1", "a".repeat(5000));
    expect(api.calls.map((call) => call.method)).toEqual([
      "sendRichMessage",
      "sendDocument",
    ]);
  });
  it("records rendered text on a rich or plain delivery", async () => {
    const api = fakeApi([], ["sendRichMessage"]);
    const channel = new TelegramChannel({
      token: "123:abc",
      allowedUserIds: [1],
      fetch: api.fetcher,
    });
    const result = await channel.send("1", "**answer** and `code`");
    expect(result.visibleText).toBe("answer and code");
    expect(api.calls.at(-1)?.body.text).toBe(result.visibleText);
  });
});

it("hides nested and unclosed markup without hiding code or leaking suffixes", () => {
  const result = renderOutgoing(
    "visible <internal-note>one <internal-note>two</internal-note> three</internal-note> `x <internal-note>literal</internal-note>`\n\n<internal-note>secret",
  );
  expect(result.visibleText).toContain("literal");
  expect(result.visibleText).not.toMatch(/one|two|three|secret/);
  expect(result.unclosedTag).toBe(true);
});
it("permits exactly 4096 plain characters after rich rejection", async () => {
  const api = fakeApi([], ["sendRichMessage"]);
  const channel = new TelegramChannel({
    token: "123:abc",
    allowedUserIds: [1],
    fetch: api.fetcher,
  });
  await channel.send("1", "a".repeat(4096));
  expect(api.calls.map((call) => call.method)).toEqual([
    "sendRichMessage",
    "sendMessage",
  ]);
});

it("splits at 500 top-level blocks without adding visible counters", () => {
  const rendered = renderOutgoing(
    Array.from({ length: 501 }, (_, i) => `paragraph ${String(i)}`).join(
      "\n\n",
    ),
  );
  expect(rendered.chunks).toHaveLength(2);
  expect(rendered.chunks[0]?.text).toContain("paragraph 499");
  expect(rendered.chunks[1]?.text).toBe("paragraph 500");
});

it("keeps escaped tags and tags in link destinations as visible Markdown", () => {
  const escaped = String.raw`\<internal-note>visible\</internal-note>`;
  const link = "[hello](https://example.com/<internal-note>)";
  expect(renderOutgoing(escaped).visibleText).toBe(escaped);
  expect(renderOutgoing(link).visibleText).toBe(link);
  expect(
    renderOutgoing("before <internal-note>secret</internal-note> after")
      .chunks[0]?.plain,
  ).toBe("before  after");
});

it("does not treat a closing tag inside a fenced block embedded in HTML as a close", () => {
  const source = "<internal-note>\n```\n</internal-note>\n```\nsecret";
  const result = renderOutgoing(source);
  expect(result.visibleText).toBe("");
  expect(result.unclosedTag).toBe(true);
});
