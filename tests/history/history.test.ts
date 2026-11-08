import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, expect, it } from "vitest";
import { around, day, search } from "../../src/history/index.js";
import { openHistory, openStore, type Store } from "../../src/store/index.js";

let directory: string;
let store: Store;
beforeEach(() => {
  directory = mkdtempSync(join(tmpdir(), "thc-history-"));
  store = openStore(join(directory, "history.sqlite"));
  store.putEntity({
    id: "e",
    directory: "/work",
    botId: "bot",
    createdAt: "2026-10-31T00:00:00Z",
  });
});
afterEach(() => {
  store.close();
  rmSync(directory, { recursive: true, force: true });
});

it("indexes only delivered messages and never exposes bridge identifiers", () => {
  store.createTurn({
    id: "internal-turn",
    entityId: "e",
    source: "telegram",
    createdAt: "2026-10-31T00:00:00Z",
  });
  store.enqueueOutbox({
    id: "internal-outbox",
    turnId: "internal-turn",
    chunk: 0,
    chatId: "c",
    text: "private hidden",
    createdAt: "2026-10-31T00:00:00Z",
  });
  store.recordVisibleMessage({
    id: "internal-message",
    chatId: "c",
    telegramMessageId: 10,
    author: "user",
    sentAt: "2026-10-31T10:00:00Z",
    text: "visible words",
    turnId: "internal-turn",
    possiblyDuplicated: false,
  });
  expect(search(store, "hidden").entries).toEqual([]);
  expect(search(store, "visible").entries).toEqual([
    {
      author: "user",
      sentAt: "2026-10-31T10:00:00Z",
      text: "visible words",
      possiblyDuplicated: false,
      remainingCharacters: 0,
    },
  ]);
});
it("paginates equal timestamps without losing entries and supports day and around", () => {
  for (let i = 0; i < 3; i++)
    store.recordVisibleMessage({
      id: `m${String(i)}`,
      chatId: "c",
      telegramMessageId: i,
      author: "user",
      sentAt: "2026-10-31T10:00:00Z",
      text: `shared ${String(i)}`,
      turnId: null,
      possiblyDuplicated: false,
    });
  const first = search(store, "shared", { limit: 2 });
  expect(first.entries).toHaveLength(2);
  expect(first.nextBefore).not.toBeNull();
  const second = search(store, "shared", {
    limit: 2,
    before: first.nextBefore ?? "",
  });
  expect(second.entries).toHaveLength(1);
  expect(day(store, "2026-10-31", { limit: 10 }).entries).toHaveLength(3);
  expect(around(store, "2026-10-31T10:00:00Z", 3)).toHaveLength(3);
});

it("searches literal punctuation and short terms over a read-only WAL connection", () => {
  store.recordVisibleMessage({
    id: "searchable",
    chatId: "c",
    telegramMessageId: 55,
    author: "assistant",
    sentAt: "2026-10-31T12:00:00Z",
    text: "codex-tmux in use",
    turnId: null,
    possiblyDuplicated: false,
  });
  const reader = openHistory(join(directory, "history.sqlite"));
  try {
    expect(search(reader, "codex-tmux").entries).toHaveLength(1);
    expect(search(reader, "in").entries).toHaveLength(1);
    expect(search(reader, "codex-tmux missing").entries).toHaveLength(0);
    expect(
      search(reader, "codex-tmux missing", { any: true }).entries,
    ).toHaveLength(1);
    expect(search(reader, "codex-tmux", { role: "user" }).entries).toHaveLength(
      0,
    );
  } finally {
    reader.close();
  }
});

it("bounds output and continues without dropping messages with the same timestamp", () => {
  for (let i = 0; i < 3; i++) {
    store.recordVisibleMessage({
      id: `long-${String(i)}`,
      chatId: "c",
      telegramMessageId: i,
      author: "user",
      sentAt: "2026-10-31T10:00:00Z",
      text: "abcdefghij",
      turnId: null,
      possiblyDuplicated: false,
    });
  }
  const first = search(store, "abc", { limit: 3, maxChars: 12 });
  expect(first.entries.map((item) => item.text)).toEqual(["abcdefghij", "ab"]);
  expect(first.entries[1]?.remainingCharacters).toBe(8);
  expect(first.nextBefore).not.toBeNull();
  const next = search(store, "abc", {
    limit: 3,
    before: first.nextBefore ?? "",
  });
  expect(next.entries).toHaveLength(1);
});
