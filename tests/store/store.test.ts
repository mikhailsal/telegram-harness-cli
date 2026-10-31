import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { afterEach, beforeEach, expect, it } from "vitest";
import {
  openStore,
  SCHEMA_VERSION,
  type Store,
} from "../../src/store/index.js";
import { migrate } from "../../src/store/schema.js";

let directory: string;
let path: string;
let store: Store;
const time = "2026-10-31T10:00:00.000Z";
beforeEach(() => {
  directory = mkdtempSync(join(tmpdir(), "thc-store-"));
  path = join(directory, "bridge.sqlite");
  store = openStore(path);
  store.putEntity({
    id: "e",
    directory: "/work",
    botId: "bot",
    createdAt: time,
  });
});
afterEach(() => {
  store.close();
  rmSync(directory, { recursive: true, force: true });
});
function turn(): void {
  store.createTurn({
    id: "t",
    entityId: "e",
    source: "telegram",
    createdAt: time,
  });
}
function inbox(): void {
  store.recordInbox({
    updateId: 10,
    entityId: "e",
    chatId: "c",
    userId: "u",
    receivedAt: time,
    payload: "text",
  });
}

it("deduplicates the inbox and preserves a committed update and cursor across restart", () => {
  inbox();
  inbox();
  store.setCursor("telegram", "11");
  expect(store.listQueuedInbox()).toHaveLength(1);
  store.close();
  store = openStore(path);
  expect(store.getInbox(10)?.payload).toBe("text");
  expect(store.getCursor("telegram")).toBe("11");
});
it("persists the chosen approval and makes competing decisions lose", () => {
  turn();
  store.createRequest({
    id: "r",
    turnId: "t",
    kind: "approval",
    payload: "command",
    createdAt: time,
  });
  expect(store.answerRequest("r", "accept")).toBe(true);
  expect(store.answerRequest("r", "decline")).toBe(false);
  store.close();
  store = openStore(path);
  expect(store.getRequest("r")).toMatchObject({
    status: "answering",
    decision: "accept",
  });
  expect(store.resolveRequest("r", "answering", "answered")).toBe(true);
  expect(store.resolveRequest("r", "answering", "answered")).toBe(false);
});
it("recovers sending and records delivered history once with duplicate flag", () => {
  turn();
  const row = {
    id: "o",
    turnId: "t",
    chunk: 0,
    chatId: "c",
    text: "answer",
    createdAt: time,
  };
  expect(store.enqueueOutbox(row)).toBe(true);
  expect(store.enqueueOutbox({ ...row, id: "other" })).toBe(false);
  expect(store.beginSend("o")).toBe(true);
  store.close();
  store = openStore(path);
  expect(store.recoverSending()).toBe(1);
  expect(store.beginSend("o")).toBe(true);
  expect(
    store.markSent("o", 12, {
      id: "m",
      chatId: "c",
      telegramMessageId: 12,
      author: "assistant",
      sentAt: time,
      text: "answer",
      turnId: "t",
      possiblyDuplicated: false,
    }),
  ).toBe(true);
  expect(store.getOutbox("o")).toMatchObject({
    status: "sent",
    possiblyDuplicated: true,
  });
  expect(store.searchMessages("answer", 10).messages).toHaveLength(1);
  expect(
    store.searchMessages("answer", 10).messages[0]?.possiblyDuplicated,
  ).toBe(true);
});
it("requeues each permanent failure only once", () => {
  turn();
  store.enqueueOutbox({
    id: "o",
    turnId: "t",
    chunk: 0,
    chatId: "c",
    text: "answer",
    createdAt: time,
  });
  store.beginSend("o");
  store.markFailed("o", "403");
  expect(store.retryFailed()).toBe(1);
  store.beginSend("o");
  store.markFailed("o", "403");
  expect(store.retryFailed()).toBe(0);
});
it("retains attachments for queued work and persists transient intent", () => {
  inbox();
  turn();
  store.putTransient({
    id: "x",
    kind: "approval",
    chatId: "c",
    createdAt: time,
    expiresAt: null,
  });
  store.putAttachment({
    updateId: 10,
    fileUniqueId: "f",
    fileKey: "k",
    path: "/files/k",
    createdAt: time,
    turnId: "t",
  });
  expect(
    store.putAttachment({
      updateId: 10,
      fileUniqueId: "f",
      fileKey: "other",
      path: "/files/other",
      createdAt: time,
      turnId: "t",
    }),
  ).toBe(false);
  expect(store.deleteAttachment(10, "f")).toBe(false);
  store.close();
  store = openStore(path);
  expect(store.listTransients()).toMatchObject([{ id: "x", messageId: null }]);
  expect(store.listRetainedAttachments()).toHaveLength(1);
});
it("upgrades every previous schema and refuses future schemas", () => {
  for (let version = 0; version < SCHEMA_VERSION; version++) {
    const priorPath = join(directory, `prior-${String(version)}.sqlite`);
    const db = new DatabaseSync(priorPath);
    migrate(db, version);
    db.close();
    const upgraded = openStore(priorPath);
    expect(upgraded.listTransients()).toEqual([]);
    upgraded.close();
  }
  const futurePath = join(directory, "future.sqlite");
  const future = new DatabaseSync(futurePath);
  future.exec(`PRAGMA user_version=${String(SCHEMA_VERSION + 1)}`);
  future.close();
  expect(() => openStore(futurePath)).toThrow(/Unsupported database version/);
});
