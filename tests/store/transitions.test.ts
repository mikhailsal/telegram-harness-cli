import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, expect, it } from "vitest";
import { openStore, type Store } from "../../src/store/index.js";

let store: Store;
let directory: string;
const at = "2026-10-31T10:00:00.000Z";
const incoming = {
  updateId: 42,
  entityId: "e",
  chatId: "c",
  userId: "u",
  receivedAt: at,
  payload: "payload",
};
const visible = {
  id: "m",
  chatId: "c",
  telegramMessageId: 20,
  author: "user" as const,
  sentAt: at,
  text: "hello",
  turnId: null,
  possiblyDuplicated: false,
};
const outgoing = {
  id: "o",
  turnId: "t",
  chunk: 0,
  chatId: "c",
  text: "result",
  createdAt: at,
};
beforeEach(() => {
  directory = mkdtempSync(join(tmpdir(), "thc-transitions-"));
  store = openStore(join(directory, "db"));
  store.putEntity({ id: "e", directory: "/work", botId: "b", createdAt: at });
  store.createTurn({
    id: "t",
    entityId: "e",
    source: "telegram",
    createdAt: at,
  });
});
afterEach(() => {
  store.close();
  rmSync(directory, { recursive: true, force: true });
});

it("atomically consumes question answers and rejects replay or resolved requests", () => {
  store.createRequest({
    id: "r",
    turnId: "t",
    kind: "question",
    payload: "?",
    createdAt: at,
  });
  store.recordInbox(incoming);
  expect(store.commitQuestionAnswer(42, "r", "first")).toBe(true);
  expect(store.commitQuestionAnswer(42, "r", "replayed")).toBe(false);
  expect(store.getCursor("question/r")).toBe("first");
  store.recordInbox({ ...incoming, updateId: 43 });
  expect(store.commitQuestionAnswer(43, "r", "complete", "answer")).toBe(true);
  expect(store.getRequest("r")).toMatchObject({
    status: "answering",
    decision: "answer",
  });
  store.recordInbox({ ...incoming, updateId: 44 });
  expect(store.commitQuestionAnswer(44, "r", "wrong")).toBe(false);
  expect(store.getInbox(44)?.status).toBe("queued");
  expect(store.getCursor("question/r")).toBe("complete");
});
it("commits inbox and visible message together and links them to a turn", () => {
  expect(store.recordIncoming(incoming, visible)).toBe(true);
  expect(store.recordIncoming(incoming, visible)).toBe(false);
  expect(store.listInbox(["queued"])).toHaveLength(1);
  expect(store.assignInbox(42, "t")).toBe(true);
  expect(store.assignInbox(42, "t")).toBe(false);
  expect(store.consumeInbox(42)).toBe(true);
  expect(store.linkMessageToTurn("m", "t")).toBe(true);
  expect(store.linkMessageToTurn("m", "t")).toBe(false);
  expect(store.messagesForTurn("t")).toHaveLength(1);
  expect(store.updateVisibleMessage("c", 20, "updated reaction")).toBe(true);
  expect(store.searchMessages("reaction", 10).messages).toHaveLength(1);
  expect(store.searchMessages("hello", 10).messages).toHaveLength(0);
  expect(() =>
    store.recordIncoming({ ...incoming, updateId: 43 }, visible),
  ).toThrow();
  expect(store.getInbox(43)).toBeUndefined();
});
it("enforces turn CAS, preserves session association, and commits terminal output together", () => {
  store.putSession({
    id: "s",
    entityId: "e",
    harness: "codex",
    externalId: "thread",
    startedAt: at,
    endedAt: null,
  });
  expect(store.getEntity("e")?.botId).toBe("b");
  expect(store.getSession("s")?.externalId).toBe("thread");
  expect(store.listSessions("e")).toHaveLength(1);
  expect(
    store.transitionTurn("t", "queued", "submitting", { sessionId: "s" }),
  ).toBe(true);
  expect(store.transitionTurn("t", "queued", "active")).toBe(false);
  expect(
    store.transitionTurn("t", "submitting", "active", {
      externalTurnId: "external",
    }),
  ).toBe(true);
  expect(
    store.finishTurnAndEnqueue("t", "active", "completed", { result: "done" }, [
      outgoing,
    ]),
  ).toBe(true);
  expect(
    store.finishTurnAndEnqueue("t", "active", "completed", {}, [outgoing]),
  ).toBe(false);
  expect(store.listTurns(["completed"])[0]).toMatchObject({
    sessionId: "s",
    externalTurnId: "external",
    result: "done",
  });
  expect(store.listOutbox(["pending"])).toHaveLength(1);
  expect(store.transitionTurn("t", "completed", "active")).toBe(false);
  expect(store.closeSession("s", at)).toBe(true);
  expect(store.closeSession("s", at)).toBe(false);
});
it("rolls back a terminal transition if output chunks conflict", () => {
  store.transitionTurn("t", "queued", "active");
  expect(() =>
    store.finishTurnAndEnqueue("t", "active", "completed", {}, [
      outgoing,
      { ...outgoing, id: "duplicate" },
    ]),
  ).toThrow();
  expect(store.getTurn("t")?.status).toBe("active");
  expect(store.listOutbox(["pending"])).toEqual([]);
});
it("persists fallback payload, notification and retry deadline", () => {
  store.enqueueOutbox({
    ...outgoing,
    payload: "rich data",
    replyToMessageId: 5,
    disableNotification: true,
  });
  expect(store.beginSend("o", at)).toBe(true);
  expect(
    store.setDeliveryFormat("o", "document", "answer.md", "file data"),
  ).toBe(true);
  expect(store.scheduleRetry("o", "429", "2026-10-31T10:01:00.000Z")).toBe(
    true,
  );
  expect(store.beginSend("o", at)).toBe(false);
  expect(store.beginSend("o", "2026-10-31T10:01:00.000Z")).toBe(true);
  expect(store.getOutbox("o")).toMatchObject({
    format: "document",
    payload: "file data",
    fileName: "answer.md",
    replyToMessageId: 5,
    disableNotification: true,
    attempts: 2,
  });
  expect(store.markFailed("o", "403")).toBe(true);
});
it("keeps request expiry and terminal states guarded by CAS", () => {
  store.createRequest({
    id: "r",
    turnId: "t",
    kind: "question",
    payload: "?",
    createdAt: at,
    expiresAt: at,
  });
  expect(store.listRequests(["pending"])[0]?.expiresAt).toBe(at);
  expect(store.resolveRequest("r", "pending", "answered")).toBe(false);
  expect(store.resolveRequest("r", "pending", "expired")).toBe(true);
  expect(store.resolveRequest("r", "pending", "cancelled")).toBe(false);
});
it("retains assigned attachments and tracks transient replacements", () => {
  store.recordIncoming(incoming, visible);
  store.putAttachment({
    updateId: 42,
    fileUniqueId: "f",
    fileKey: "k",
    path: "/file",
    createdAt: at,
    turnId: null,
  });
  store.assignInbox(42, "t");
  expect(store.listRetainedAttachments()).toHaveLength(1);
  expect(store.listAttachments()).toHaveLength(1);
  expect(store.assignAttachment(42, "f", "t")).toBe(true);
  expect(store.getAttachment(42, "f")?.turnId).toBe("t");
  store.putTransient({
    id: "x",
    kind: "welcome",
    chatId: "c",
    createdAt: at,
    expiresAt: at,
  });
  expect(store.setTransientMessage("x", 1, at)).toBe(true);
  expect(store.replaceTransientMessage("x", 1, 2, at, null)).toBe(true);
  expect(store.replaceTransientMessage("x", 1, 3, at, null)).toBe(false);
  expect(store.deleteTransient("x")).toBe(true);
});
