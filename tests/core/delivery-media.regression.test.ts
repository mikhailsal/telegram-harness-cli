import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, it } from "vitest";
import { TelegramChannel } from "../../src/channels/index.js";
import { parseConfig } from "../../src/core/config.js";
import type { RuntimeContext } from "../../src/core/context.js";
import { Delivery } from "../../src/core/delivery.js";
import { openStore } from "../../src/store/index.js";

async function fixture(failPhoto = false, rateLimitPhoto = false) {
  const root = await mkdtemp(join(tmpdir(), "thc-outgoing-"));
  const methods: string[] = [];
  const uploads: Buffer[] = [];
  const fetcher = transport(methods, uploads, failPhoto, rateLimitPhoto);
  const store = openStore(":memory:");
  const now = new Date("2026-11-02T00:00:00Z");
  const turn = seedTurn(store, root, now);
  const channel = new TelegramChannel({
    token: "123:synthetic",
    allowedUserIds: [1],
    fetch: fetcher,
  });
  const ctx = {
    directory: root,
    store,
    channel,
    config: parseConfig({
      token: { env: "TOKEN" },
      allowlist: [1],
      sandbox: "read-only",
    }),
    now: () => now,
    log: () => undefined,
  } as unknown as RuntimeContext;
  return {
    root,
    store,
    turn,
    methods,
    uploads,
    advance: (ms: number) => now.setTime(now.getTime() + ms),
    delivery: new Delivery(ctx),
    async close() {
      store.close();
      await rm(root, { recursive: true, force: true });
    },
  };
}
function seedTurn(
  store: ReturnType<typeof openStore>,
  root: string,
  now: Date,
) {
  store.putEntity({
    id: "e",
    directory: root,
    botId: "123",
    createdAt: now.toISOString(),
  });
  store.createTurn({
    id: "turn",
    entityId: "e",
    source: "telegram",
    createdAt: now.toISOString(),
  });
  store.transitionTurn("turn", "queued", "active");
  const turn = store.getTurn("turn");
  if (!turn) throw new Error("missing turn");
  return turn;
}
function transport(
  methods: string[],
  uploads: Buffer[],
  failPhoto: boolean,
  rateLimitPhoto: boolean,
): typeof fetch {
  let limited = false;
  return async (input, init) => {
    methods.push(methodName(input));
    if (
      ["sendPhoto", "sendDocument"].includes(methods.at(-1) ?? "") &&
      init?.body
    )
      uploads.push(Buffer.from(await new Response(init.body).arrayBuffer()));
    if (rateLimitPhoto && !limited && methods.at(-1) === "sendPhoto") {
      limited = true;
      return Response.json({
        ok: false,
        error_code: 429,
        description: "retry",
        parameters: { retry_after: 1 },
      });
    }
    if (failPhoto && methods.at(-1) === "sendPhoto")
      return Promise.resolve(
        Response.json({
          ok: false,
          error_code: 400,
          description: "invalid photo",
        }),
      );
    return Promise.resolve(
      Response.json({
        ok: true,
        result: {
          message_id: methods.length,
          date: 1,
          chat: { id: 1, type: "private" },
        },
      }),
    );
  };
}
function methodName(input: RequestInfo | URL): string {
  const url =
    typeof input === "string"
      ? input
      : input instanceof URL
        ? input.href
        : input.url;
  return url.split("/").at(-1) ?? "";
}
it("delivers explicitly linked workspace image and document after the readable answer", async () => {
  const f = await fixture();
  const image = join(f.root, "manga.png"),
    prompt = join(f.root, "prompt.txt");
  await writeFile(image, Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]));
  await writeFile(prompt, "synthetic prompt");
  try {
    expect(
      f.delivery.finish(
        f.turn,
        "completed",
        `Готово — [разворот в PNG](${image}). Читается **справа налево**.\n\n[промпт](${prompt})`,
      ),
    ).toBe(true);
    expect(f.store.listOutbox(["pending"])).toHaveLength(3);
    await f.delivery.flush();
    expect(f.methods).toEqual(["sendRichMessage", "sendPhoto", "sendDocument"]);
    expect(f.uploads[0]?.includes(Buffer.from([137, 80, 78, 71]))).toBe(true);
    expect(f.uploads[1]?.includes(Buffer.from("synthetic prompt"))).toBe(true);
    expect(f.store.listOutbox(["sent"])).toHaveLength(3);
  } finally {
    await f.close();
  }
});
it("falls back to a document when Telegram rejects a photo", async () => {
  const f = await fixture(true);
  const image = join(f.root, "image.png");
  await writeFile(image, "image");
  try {
    f.delivery.finish(f.turn, "completed", `[photo](${image})`);
    await f.delivery.flush();
    expect(f.methods).toEqual(["sendRichMessage", "sendPhoto", "sendDocument"]);
    expect(f.store.listOutbox(["failed"])).toEqual([]);
  } finally {
    await f.close();
  }
});
it("does not upload a changed local file during a later delivery attempt", async () => {
  const f = await fixture(false, true);
  const image = join(f.root, "image.png");
  await writeFile(image, "original");
  try {
    f.delivery.finish(f.turn, "completed", `[photo](${image})`);
    await f.delivery.flush();
    expect(f.store.listOutbox(["pending"])).toHaveLength(1);
    await writeFile(image, "replaced");
    f.advance(1000);
    await f.delivery.flush();
    expect(f.methods).toEqual(["sendRichMessage", "sendPhoto"]);
    expect(f.store.listOutbox(["failed"])).toHaveLength(1);
  } finally {
    await f.close();
  }
});
