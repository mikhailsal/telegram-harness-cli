import { expect, it } from "vitest";
import { renderOutgoing } from "../../src/channels/render.js";
import { TelegramChannel } from "../../src/channels/index.js";

it("renders ordinary Markdown as balanced Telegram HTML and sends it in regular mode", async () => {
  const result = renderOutgoing(
    "**bold** _italic_ ~~gone~~ `code` [site](https://example.com)",
    [],
    undefined,
    "regular",
  );
  expect(result.chunks[0]).toMatchObject({ kind: "message", style: "html" });
  expect(result.chunks[0]?.text).toContain("<b>bold</b>");
  expect(result.chunks[0]?.text).toContain("<i>italic</i>");
  expect(result.chunks[0]?.text).toContain(
    '<a href="https://example.com">site</a>',
  );
  const calls: { method: string; body: Record<string, unknown> }[] = [];
  const fetcher: typeof fetch = (input, init) => {
    const url =
      typeof input === "string"
        ? input
        : input instanceof URL
          ? input.href
          : input.url;
    calls.push({
      method: url.split("/").at(-1) ?? "",
      body: JSON.parse(
        typeof init?.body === "string" ? init.body : "{}",
      ) as Record<string, unknown>,
    });
    return Promise.resolve(
      Response.json({
        ok: true,
        result: { message_id: 1, date: 1, chat: { id: 1, type: "private" } },
      }),
    );
  };
  const channel = new TelegramChannel({
    token: "123:synthetic",
    allowedUserIds: [1],
    fetch: fetcher,
  });
  await channel.send("1", "**bold**", { format: "regular" });
  expect(calls[0]).toMatchObject({
    method: "sendMessage",
    body: { text: "<b>bold</b>", parse_mode: "HTML" },
  });
});

it("escapes raw markup and unsafe links while hiding configured tags outside code", () => {
  const input =
    "<script>bad</script>\n\n[unsafe](javascript:alert) [safe](https://example.com/?x=1&y=2) <internal-note>SECRET</internal-note> ` <internal-note>visible</internal-note> `";
  const result = renderOutgoing(input, ["internal-note"], undefined, "regular");
  const html = result.chunks.map((chunk) => chunk.text).join("");
  expect(html).toContain("&lt;script&gt;");
  expect(html).not.toContain('href="javascript:');
  expect(html).toContain('href="https://example.com/?x=1&amp;y=2"');
  expect(html).not.toContain("SECRET");
  expect(html).toContain("&lt;internal-note&gt;visible&lt;/internal-note&gt;");
});
it("renders lists, table, quote, spoiler and code without unsupported tags", () => {
  const result = renderOutgoing(
    "> quoted\n\n- one\n- two\n\n| a | b |\n| - | - |\n| c | d |\n\n||secret||\n\n```ts\nconst x = 1 < 2\n```",
    [],
    undefined,
    "regular",
  );
  const html = result.chunks.map((chunk) => chunk.text).join("");
  expect(html).toContain("<blockquote>quoted</blockquote>");
  expect(html).toContain("• one\n• two");
  expect(html).toContain("a | b\nc | d");
  expect(html).toContain('<span class="tg-spoiler">secret</span>');
  expect(html).toContain("<pre><code>const x = 1 &lt; 2</code></pre>");
});
it("splits long styled text into balanced HTML chunks without breaking Unicode", () => {
  const result = renderOutgoing(
    `**${"a".repeat(4095)}😀${"b".repeat(4096)}**`,
    [],
    undefined,
    "regular",
  );
  expect(result.chunks.length).toBeGreaterThan(1);
  for (const chunk of result.chunks) {
    expect(chunk.plain.length).toBeLessThanOrEqual(4096);
    expect(chunk.text.startsWith("<b>")).toBe(true);
    expect(chunk.text.endsWith("</b>")).toBe(true);
  }
  expect(result.chunks.map((chunk) => chunk.plain).join("")).toBe(
    `${"a".repeat(4095)}😀${"b".repeat(4096)}`,
  );
});
it("uses the matching draft family and only consumes its corresponding final message", async () => {
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
        result: method.endsWith("Draft")
          ? true
          : { message_id: 1, date: 1, chat: { id: 1, type: "private" } },
      }),
    );
  };
  const channel = new TelegramChannel({
    token: "123:synthetic",
    allowedUserIds: [1],
    fetch: fetcher,
  });
  await channel.draft(1, 42, "working", "regular");
  expect(channel.hasDraft("1", 42, "regular")).toBe(true);
  expect(channel.hasDraft("1", 42, "rich")).toBe(false);
  await channel.bot.api.sendRichMessage(1, { markdown: "other" });
  expect(channel.hasDraft("1", 42, "regular")).toBe(true);
  await channel.finalizeDraft(1, 42, "working", "regular");
  expect(channel.hasDraft("1", 42)).toBe(false);
  expect(methods).toEqual([
    "sendMessageDraft",
    "sendRichMessage",
    "sendMessage",
  ]);
});
it("falls back to plain text after an HTML parse rejection without consuming a rich draft", async () => {
  const calls: { method: string; body: Record<string, unknown> }[] = [];
  const fetcher: typeof fetch = (input, init) => {
    const url =
      typeof input === "string"
        ? input
        : input instanceof URL
          ? input.href
          : input.url;
    const method = url.split("/").at(-1) ?? "";
    const body = JSON.parse(
      typeof init?.body === "string" ? init.body : "{}",
    ) as Record<string, unknown>;
    calls.push({ method, body });
    if (method === "sendMessage" && body.parse_mode === "HTML")
      return Promise.resolve(
        Response.json({ ok: false, error_code: 400, description: "bad HTML" }),
      );
    return Promise.resolve(
      Response.json({
        ok: true,
        result: method.endsWith("Draft")
          ? true
          : { message_id: 1, date: 1, chat: { id: 1, type: "private" } },
      }),
    );
  };
  const channel = new TelegramChannel({
    token: "123:synthetic",
    allowedUserIds: [1],
    fetch: fetcher,
  });
  await channel.draft(1, 5, "thinking");
  const delivered = await channel.send("1", "**bold**", { format: "regular" });
  expect(delivered.visibleText).toBe("bold");
  expect(calls.map((call) => call.method)).toEqual([
    "sendRichMessageDraft",
    "sendMessage",
    "sendMessage",
  ]);
  expect(calls[2]?.body).toMatchObject({ text: "bold" });
  expect(calls[2]?.body.parse_mode).toBeUndefined();
  expect(channel.hasDraft("1", 5, "rich")).toBe(true);
});
it("keeps visible HTML around hidden ranges and handles nested quotes, images and line breaks", () => {
  const html = renderOutgoing(
    "<div>before <internal-note>SECRET</internal-note> after</div>",
    ["internal-note"],
    undefined,
    "regular",
  );
  expect(html.chunks[0]?.text).toContain("before");
  expect(html.chunks[0]?.text).toContain("after");
  expect(html.chunks[0]?.text).not.toContain("SECRET");
  const other = renderOutgoing(
    "> outer\n> > inner\n\n![diagram](https://example.com/i.png)\nline  \nbreak",
    [],
    undefined,
    "regular",
  );
  const text = other.chunks.map((chunk) => chunk.text).join("");
  expect(text).toContain('<a href="https://example.com/i.png">diagram</a>');
  expect(text).toContain("line\nbreak");
  expect(text).not.toContain("<blockquote><blockquote>");
});
it("keeps giant code as a file while splitting adjacent regular text", () => {
  const result = renderOutgoing(
    `before\n\n\`\`\`\n${"x".repeat(33000)}\n\`\`\`\n\nafter`,
    [],
    undefined,
    "regular",
  );
  expect(result.chunks.map((chunk) => chunk.kind)).toEqual([
    "message",
    "file",
    "message",
  ]);
});
it("does not turn a regular 429 into a plain fallback", async () => {
  let attempts = 0;
  const fetcher: typeof fetch = () => {
    attempts++;
    return Promise.resolve(
      Response.json({
        ok: false,
        error_code: 429,
        description: "retry",
        parameters: { retry_after: 3 },
      }),
    );
  };
  const channel = new TelegramChannel({
    token: "123:synthetic",
    allowedUserIds: [1],
    fetch: fetcher,
  });
  await expect(
    channel.send("1", "**bold**", { format: "regular" }),
  ).rejects.toThrow();
  expect(attempts).toBe(1);
});

it("resolves formatted references safely without revealing hidden definitions", () => {
  const source = [
    "See [**manual**][ ReF ] and ![diagram][img] and [unsafe][bad].",
    "",
    "[ref]: https://example.com/manual?x=1&y=2",
    "[img]: https://example.com/image.png",
    "[bad]: javascript:alert(1)",
    "",
    "<internal-note>",
    "[secret]: https://user:credential@example.com/private",
    "</internal-note>",
    "",
    "[secret] and [missing][unknown]",
  ].join("\n");
  const result = renderOutgoing(
    source,
    ["internal-note"],
    undefined,
    "regular",
  );
  const html = result.chunks.map((chunk) => chunk.text).join("\n");
  expect(html).toContain(
    '<a href="https://example.com/manual?x=1&amp;y=2"><b>manual</b></a>',
  );
  expect(html).toContain('<a href="https://example.com/image.png">diagram</a>');
  expect(html).toContain("unsafe");
  expect(html).not.toContain("javascript:");
  expect(html).not.toContain("credential");
  expect(html).toContain("[secret] and [missing][unknown]");
});

it("keeps a reference literal when its parsed definition is hidden", () => {
  const source =
    "[x][sec]\n\n<internal-note>\n\n[sec]: https://user:secret@example.com\n\n</internal-note>";
  const result = renderOutgoing(
    source,
    ["internal-note"],
    undefined,
    "regular",
  );
  expect(result.chunks.map((chunk) => chunk.text).join("")).toBe("[x][sec]");
});

it("keeps a multiline quote intact across bold and italic phrases", () => {
  const result = renderOutgoing(
    "> Start **bold** and _italic_\n> next line",
    [],
    undefined,
    "regular",
  );
  expect(result.chunks).toHaveLength(1);
  expect(result.chunks[0]?.text).toBe(
    "<blockquote>Start <b>bold</b> and <i>italic</i>\nnext line</blockquote>",
  );
});
it("reopens one balanced quote at message boundaries without losing Unicode", () => {
  const result = renderOutgoing(
    `> **${"a".repeat(4095)}😀tail**`,
    [],
    undefined,
    "regular",
  );
  expect(result.chunks).toHaveLength(2);
  for (const chunk of result.chunks) {
    expect(chunk.plain.length).toBeLessThanOrEqual(4096);
    expect(chunk.text).toMatch(/^<blockquote><b>.*<\/b><\/blockquote>$/u);
  }
  expect(result.chunks.map((chunk) => chunk.plain).join("")).toBe(
    `${"a".repeat(4095)}😀tail`,
  );
});
it("keeps code and links outside quote entities that Telegram cannot nest", () => {
  const result = renderOutgoing(
    "> Before `code` and [site](https://example.com) after",
    [],
    undefined,
    "regular",
  );
  expect(result.chunks[0]?.text).toBe(
    '<blockquote>Before </blockquote><code>code</code><blockquote> and </blockquote><a href="https://example.com">site</a><blockquote> after</blockquote>',
  );
  expect(result.chunks[0]?.plain).toBe("Before code and site after");
});
