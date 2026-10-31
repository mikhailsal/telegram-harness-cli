import { expect, it } from "vitest";
import { join } from "node:path";
import {
  acceptReaction,
  recordReaction,
  replyContext,
  trackReaction,
} from "../../src/core/interactions.js";
import type { RuntimeContext } from "../../src/core/context.js";
import type { ChannelEvent } from "../../src/channels/types.js";
import { openStore } from "../../src/store/index.js";

type Reaction = Extract<ChannelEvent, { kind: "reaction" }>;
type Message = Extract<ChannelEvent, { kind: "message" }>;
const at = "2026-01-01T12:00:00.000Z";
function fixture(path = ":memory:") {
  const store = openStore(path);
  store.putEntity({ id: "e", directory: "/work", botId: "123", createdAt: at });
  store.createTurn({
    id: "t",
    entityId: "e",
    source: "telegram",
    createdAt: at,
  });
  store.recordVisibleMessage({
    id: "question",
    chatId: "1",
    telegramMessageId: 3,
    author: "user",
    sentAt: at,
    text: "Original task",
    turnId: "t",
    possiblyDuplicated: false,
  });
  store.recordVisibleMessage({
    id: "answer",
    chatId: "1",
    telegramMessageId: 4,
    author: "assistant",
    sentAt: at,
    text: "The visible answer",
    turnId: "t",
    possiblyDuplicated: false,
  });
  return { store, ctx: { store } as RuntimeContext };
}
function message(): Message {
  return {
    kind: "message",
    updateId: 20,
    chatId: "1",
    userId: "1",
    messageId: 9,
    sentAt: at,
    language: "en",
    text: "Why?",
    attachments: [],
    reply: "Telegram excerpt",
    replyToMessageId: 4,
    replyToBot: true,
  };
}
function reaction(updateId: number, reactions: string[], target = 4): Reaction {
  return {
    kind: "reaction",
    updateId,
    chatId: "1",
    userId: "1",
    messageId: target,
    sentAt: at,
    language: "en",
    reactions,
  };
}
function queue(store: ReturnType<typeof openStore>, event: Reaction): void {
  store.recordInbox({
    updateId: event.updateId,
    entityId: "e",
    chatId: event.chatId,
    userId: event.userId,
    receivedAt: at,
    payload: JSON.stringify(event),
  });
}

it("enriches a reply to a known bot answer and preserves the selected quote", () => {
  const { store, ctx } = fixture();
  try {
    const enriched = replyContext(ctx, {
      ...message(),
      quote: "selected excerpt",
    });
    expect(enriched).toMatchObject({
      reply: "The visible answer",
      quote: "selected excerpt",
      replyAuthor: "assistant",
      replySentAt: at,
      replySource: { sentAt: at, text: "Original task" },
    });
    expect(replyContext(ctx, { ...message(), replyToBot: false }).reply).toBe(
      "Telegram excerpt",
    );
    expect(
      replyContext(ctx, { ...message(), replyToMessageId: 999 }).reply,
    ).toBe("Telegram excerpt");
    expect(replyContext(ctx, { ...message(), chatId: "2" }).reply).toBe(
      "Telegram excerpt",
    );
    store.recordVisibleMessage({
      id: "dup",
      chatId: "1",
      telegramMessageId: 5,
      author: "assistant",
      sentAt: at,
      text: "duplicate",
      turnId: "t",
      possiblyDuplicated: true,
    });
    expect(replyContext(ctx, { ...message(), replyToMessageId: 5 }).reply).toBe(
      "duplicate",
    );
  } finally {
    store.close();
  }
});

it("keeps reaction context and deduplication across restart", async () => {
  const { mkdtemp, rm } = await import("node:fs/promises");
  const { tmpdir } = await import("node:os");
  const root = await mkdtemp(join(tmpdir(), "thc-reaction-"));
  const path = join(root, "store.db");
  try {
    const first = fixture(path);
    const event = reaction(55, ["👍"]);
    queue(first.store, event);
    recordReaction(first.ctx, event);
    first.store.close();
    const restored = openStore(path);
    try {
      recordReaction({ store: restored } as RuntimeContext, event);
      expect(restored.getInbox(55)?.status).toBe("consumed");
      expect(restored.getCursor("next-reaction")?.split("\n")).toHaveLength(1);
      expect(restored.searchMessages("Reacted", 10).messages).toHaveLength(1);
    } finally {
      restored.close();
    }
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

function insertDecisionExample(store: ReturnType<typeof openStore>): void {
  expect(
    store.recordDecisionHistory({
      id: "request-summary/example",
      chatId: "1",
      author: "system",
      sentAt: at,
      text: "You allowed a command",
      turnId: "t",
      possiblyDuplicated: false,
    }),
  ).toBe(true);
  store.setCursor("request-history/example", "");
  expect(
    store.recordDecisionHistory({
      id: "request-summary/example",
      chatId: "1",
      author: "system",
      sentAt: at,
      text: "duplicate",
      turnId: "t",
      possiblyDuplicated: false,
    }),
  ).toBe(false);
  expect(store.getCursor("request-history/example")).toBe("1");
}
it("records and accumulates reactions once, including a removal", () => {
  const { store, ctx } = fixture();
  try {
    const first = reaction(11, ["👍"]),
      second = reaction(12, []);
    queue(store, first);
    queue(store, second);
    recordReaction(ctx, first);
    recordReaction(ctx, first);
    insertDecisionExample(store);
    recordReaction(ctx, second);
    expect(store.getInbox(11)?.status).toBe("consumed");
    const context = store.getCursor("next-reaction") ?? "";
    expect(context).toContain("Reacted 👍 to answer");
    expect(context).toContain("Reaction removed to answer");
    expect(context).toContain("The visible answer");
    expect(context.split("\n")).toHaveLength(2);
    expect(store.searchMessages("Reacted", 10).messages).toHaveLength(1);
    const history = store
      .messagesForTurn("t")
      .filter((row) => row.id.startsWith("reaction/"));
    expect(history).toHaveLength(2);
    expect(history.every((row) => row.telegramMessageId < 0)).toBe(true);
    const internal = store
      .messagesForTurn("t")
      .filter((row) => row.telegramMessageId < 0);
    expect(new Set(internal.map((row) => row.telegramMessageId)).size).toBe(3);
    expect(store.latestVisibleMessageId("1")).toBe(4);
    expect(store.latestVisibleMessageId("2")).toBeUndefined();
  } finally {
    store.close();
  }
});

it("acknowledges captured reactions without losing newer ones", () => {
  const { store, ctx } = fixture();
  try {
    const first = reaction(40, ["👍"]),
      second = reaction(41, ["❤️"]);
    queue(store, first);
    recordReaction(ctx, first);
    const captured = store.getCursor("next-reaction");
    trackReaction(ctx, "t");
    expect(store.getCursor("submitted-reaction/t")).toBe(captured);
    queue(store, second);
    recordReaction(ctx, second);
    trackReaction(ctx, "t");
    expect(store.getCursor("submitted-reaction/t")).toBe(captured);
    acceptReaction(ctx, "t");
    expect(store.getCursor("next-reaction")).toContain("❤️");
    expect(store.getCursor("submitted-reaction/t")).toBe("");
    trackReaction(ctx, "next");
    acceptReaction(ctx, "next");
    expect(store.getCursor("next-reaction")).toBe("");
  } finally {
    store.close();
  }
});

it("ignores unknown targets and rolls back the inbox if history conflicts", () => {
  const { store, ctx } = fixture();
  try {
    const unknown = reaction(13, ["👍"], 99);
    queue(store, unknown);
    recordReaction(ctx, unknown);
    expect(store.getInbox(13)?.status).toBe("consumed");
    expect(store.getCursor("next-reaction")).toBeUndefined();
    const event = reaction(14, ["👍"]);
    queue(store, event);
    store.recordVisibleMessage({
      id: "reaction/14",
      chatId: "other",
      telegramMessageId: 10,
      author: "user",
      sentAt: at,
      text: "existing",
      turnId: null,
      possiblyDuplicated: false,
    });
    expect(() => {
      recordReaction(ctx, event);
    }).toThrow("Reaction history already exists");
    expect(store.getInbox(14)?.status).toBe("queued");
    expect(store.getCursor("next-reaction")).toBeUndefined();
  } finally {
    store.close();
  }
});
