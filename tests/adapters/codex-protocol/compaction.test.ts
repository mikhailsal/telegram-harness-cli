import { expect, it } from "vitest";
import { itemProgress } from "../../../src/adapters/codex-protocol/progress.js";
import { historyEvents } from "../../../src/adapters/codex-protocol/events.js";

it("associates compaction progress with its actual protocol turn", () => {
  expect(
    itemProgress("thread", "compact-turn", {
      id: "compact-item",
      type: "contextCompaction",
    }),
  ).toEqual({
    kind: "progress",
    sessionId: "thread",
    turnId: "compact-turn",
    cursor: "compact-turn/progress/compact-item",
    data: { kind: "compacting" },
  });
  expect(
    itemProgress("thread", "other-turn", { id: "unknown", type: "unrelated" }),
  ).toBeUndefined();
});
it("preserves compaction identity when replaying a completed turn", () => {
  const events = historyEvents("thread", {
    id: "turn",
    status: "completed",
    items: [
      {
        id: "input",
        type: "userMessage",
        content: [{ type: "text", text: "task" }],
      },
      { id: "compact", type: "contextCompaction" },
      { id: "answer", type: "agentMessage", text: "result" },
    ],
  });
  expect(events.map((event) => event.kind)).toEqual([
    "turn-started",
    "input",
    "progress",
    "output",
    "turn-completed",
  ]);
  expect(events[2]).toMatchObject({
    turnId: "turn",
    data: { kind: "compacting" },
  });
  expect(events[3]?.text).toBe("result");
});
