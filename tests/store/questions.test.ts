import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, expect, it } from "vitest";
import { Store } from "../../src/store/index.js";
import type { QuestionAnswer } from "../../src/store/questions.js";

let root: string;
let store: Store;
const now = "2026-11-25T10:00:00.000Z";
beforeEach(async () => {
  root = await mkdtemp(join(tmpdir(), "thc-questions-"));
  store = new Store(join(root, "store.db"));
  store.putEntity({
    id: "entity",
    directory: root,
    botId: "bot",
    createdAt: now,
  });
  store.recordInbox({
    updateId: 9,
    entityId: "entity",
    chatId: "1",
    userId: "1",
    receivedAt: now,
    payload: "{}",
  });
  store.setCursor("async-question/q", "open");
});
afterEach(async () => {
  store.close();
  await rm(root, { recursive: true, force: true });
});
function answer(patch: Partial<QuestionAnswer> = {}): QuestionAnswer {
  return {
    updateId: 9,
    row: "async-question/q",
    expected: "open",
    answered: "answered",
    turn: {
      id: "turn",
      entityId: "entity",
      source: "telegram",
      createdAt: now,
    },
    reply: "<reply>",
    history: {
      id: "question-answer/9",
      chatId: "1",
      author: "user",
      sentAt: now,
      text: "dogs",
      possiblyDuplicated: false,
    },
    ...patch,
  };
}

// Value: protects=a button answer is all-or-nothing: question, turn, inbox, reply and history move together; fails_when=a lost race or another entity's update still creates a turn; why_new=the store method is new; seam=SQLite; no_claim=engine routing
it("records a button answer as one turn, and refuses a stale or foreign one", () => {
  // protects=a changed question or another entity's update leaves everything as it was; fails_when=the compare-and-set or the entity check is skipped; no_claim=none beyond protects
  expect(store.answerQuestion(answer({ expected: "other" }))).toBe(false);
  expect(
    store.answerQuestion(
      answer({ turn: { ...answer().turn, entityId: "elsewhere" } }),
    ),
  ).toBe(false);
  expect(store.getTurn("turn")).toBeUndefined();
  expect(store.getCursor("async-question/q")).toBe("open");
  // protects=the accepted answer updates the question, queues its turn with the tap assigned, and keeps the reply and history; fails_when=any part is missing; no_claim=none beyond protects
  expect(store.answerQuestion(answer())).toBe(true);
  expect(store.getCursor("async-question/q")).toBe("answered");
  expect(store.getTurn("turn")?.status).toBe("queued");
  expect(store.getInbox(9)).toMatchObject({
    status: "assigned",
    turnId: "turn",
  });
  expect(store.questionReply("turn")).toBe("<reply>");
  expect(store.messagesForTurn("turn").map((row) => row.text)).toEqual([
    "dogs",
  ]);
  // protects=the same tap cannot answer twice; fails_when=an assigned update is accepted again; no_claim=none beyond protects
  expect(
    store.answerQuestion(
      answer({ expected: "answered", turn: { ...answer().turn, id: "again" } }),
    ),
  ).toBe(false);
});
