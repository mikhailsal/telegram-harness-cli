import { readFile } from "node:fs/promises";
import { describe, expect, it } from "vitest";
import {
  historyEvents,
  itemEvent,
} from "../../../src/adapters/codex-protocol/events.ts";
import {
  notification,
  turnList,
} from "../../../src/adapters/codex-protocol/schema.ts";
import {
  itemProgress,
  ReasoningProgress,
} from "../../../src/adapters/codex-protocol/progress.ts";

interface Fixture {
  sample: string;
  message: { result?: unknown; params?: unknown; method?: string };
}
async function fixtures(): Promise<Fixture[]> {
  const raw = await readFile(
    "tests/fixtures/codex-protocol/messages.jsonl",
    "utf8",
  );
  return raw
    .trim()
    .split("\n")
    .map((line) => JSON.parse(line) as Fixture);
}

describe("protocol fixtures", () => {
  it("normalizes full history and keeps the recorded client correlation", async () => {
    const sample = (await fixtures()).find(
      (entry) => entry.sample === "turns_list",
    );
    const result = turnList.parse(sample?.message.result);
    const first = result.data[0];
    expect(first).toBeDefined();
    if (!first) throw new Error("missing fixture turn");
    const events = historyEvents("thread", first);
    expect(events.map((event) => event.kind)).toEqual([
      "turn-started",
      "input",
      "output",
      "turn-completed",
    ]);
    expect(events[1]).toMatchObject({
      correlationId: "thc-spike-input-1",
      text: "Reply THC_SPIKE_ONE",
    });
    expect(events[2]).toMatchObject({ text: "THC_SPIKE_ONE" });
  });
  it("accepts real item and approval notifications while ignoring unrelated items", async () => {
    const samples = await fixtures();
    const item = notification.parse(
      samples.find((entry) => entry.sample === "item/completed")?.message,
    );
    expect(
      item.params.item &&
        itemEvent(
          item.params.threadId,
          item.params.turnId ?? "",
          item.params.item,
        )?.kind,
    ).toBe("input");
    expect(
      notification.parse(
        samples.find((entry) => entry.sample === "approval_a")?.message,
      ).id,
    ).toBe(0);
    expect(
      itemEvent("t", "turn", { id: "x", type: "commandExecution" }),
    ).toBeUndefined();
  });
});

it("maps started command and reasoning items to ephemeral progress", () => {
  expect(
    itemProgress("s", "t", {
      id: "1",
      type: "commandExecution",
      command: "npm test",
    }),
  ).toMatchObject({
    kind: "progress",
    data: { kind: "command", detail: "npm test" },
  });
  expect(itemProgress("s", "t", { id: "2", type: "reasoning" })).toMatchObject({
    kind: "progress",
    data: { kind: "thinking" },
  });
  expect(itemProgress("s", "t", { id: "3", type: "fileChange" })).toMatchObject(
    { data: { kind: "file-change" } },
  );
});

it("summarizes reasoning deltas and recognizes file reads", () => {
  const reasoning = new ReasoningProgress();
  const notificationFor = (itemId: string, delta: string) =>
    notification.parse({
      method: "item/reasoning/summaryTextDelta",
      params: { threadId: "s", turnId: "t", itemId, delta },
    });
  expect(
    reasoning.update("s", notificationFor("1", "**Checking")),
  ).toMatchObject({ data: { detail: "Checking" } });
  expect(
    reasoning.update("s", notificationFor("1", " tests**\nLong text")),
  ).toMatchObject({ data: { detail: "Checking tests" } });
  expect(
    reasoning.update("s", notificationFor("2", "Next item")),
  ).toMatchObject({ data: { detail: "Next item" } });
  expect(
    itemProgress("s", "t", {
      id: "3",
      type: "commandExecution",
      commandActions: [{ type: "read", path: "src/a.ts" }],
    }),
  ).toMatchObject({ data: { kind: "read", detail: "src/a.ts" } });
  expect(
    itemProgress("s", "t", {
      id: "4",
      type: "commandExecution",
      commandActions: [{ type: "read", name: "a.ts" }],
    }),
  ).toMatchObject({ data: { detail: "a.ts" } });
  expect(
    reasoning.update(
      "s",
      notification.parse({
        method: "item/reasoning/summaryTextDelta",
        params: { threadId: "s" },
      }),
    ),
  ).toBeUndefined();
});

// Value: protects=the status shows the newest reasoning heading when Codex streams several summary parts; fails_when=parts are glued and the status reads "A****B"; why_new=the Telegram check showed glued headings; seam=ReasoningProgress.update; no_claim=Codex's choice of headings
it("starts a new reasoning title for each summary part", () => {
  const reasoning = new ReasoningProgress();
  const part = (summaryIndex: number, delta: string) =>
    reasoning.update(
      "s",
      notification.parse({
        method: "item/reasoning/summaryTextDelta",
        params: {
          threadId: "s",
          turnId: "t",
          itemId: "1",
          delta,
          summaryIndex,
        },
      }),
    );
  expect(part(0, "**Reading the config**")).toMatchObject({
    data: { detail: "Reading the config" },
  });
  expect(part(1, "**Checking")).toMatchObject({ data: { detail: "Checking" } });
  expect(part(1, " the tests**")).toMatchObject({
    data: { detail: "Checking the tests" },
  });
});

describe("turn chains", () => {
  it("carries the root turn of a recovery turn in its live start event", () => {
    const message = notification.parse({
      method: "turn/started",
      params: {
        threadId: "thread",
        turn: {
          id: "recovery",
          status: "inProgress",
          items: [],
          rootTurnId: "original",
        },
      },
    });
    if (!message.params.turn) throw new Error("missing turn");
    // protects=a daemon_recovery turn names the turn it continues; fails_when=rootTurnId is dropped by the schema or the event; no_claim=that Codex sends rootTurnId on turn/started (payload shaped after the 0.169.0 schema)
    expect(historyEvents("thread", message.params.turn)).toEqual([
      {
        sessionId: "thread",
        turnId: "recovery",
        kind: "turn-started",
        cursor: "recovery/start",
        data: { rootTurnId: "original" },
      },
    ]);
  });
  it("omits the root turn when it is missing or the turn itself", () => {
    const parsed = turnList.parse({
      data: [
        { id: "a", status: "completed", items: [], rootTurnId: null },
        { id: "b", status: "completed", items: [], rootTurnId: "b" },
        { id: "c", status: "completed", items: [] },
      ],
    });
    // protects=ordinary turns keep a data-free start event; fails_when=a null or self root is reported as a chain; no_claim=none beyond protects
    for (const turn of parsed.data)
      expect(historyEvents("thread", turn)[0]).not.toHaveProperty("data");
  });
});

describe("async questions", () => {
  it("marks a request_user_input_async message and leaves other answers plain", () => {
    // Shaped after Codex 0.171.0's item/completed for request_user_input_async.
    const asked = notification.parse({
      method: "item/completed",
      params: {
        threadId: "thread",
        turnId: "turn",
        item: {
          type: "agentMessage",
          id: "call_1",
          text: "Cats or dogs?\n- cats\n- dogs",
          phase: "final_answer",
          delivery: "async",
          questions: [{ title: "Cats or dogs?", options: ["cats", "dogs"] }],
        },
      },
    });
    if (!asked.params.item) throw new Error("missing item");
    // protects=the async question reaches the engine marked as such, with the tool call its answer names and the options for buttons; fails_when=the schema or the event drops delivery, the item ID or the questions; no_claim=how the engine delivers it (core tests cover that)
    expect(itemEvent("thread", "turn", asked.params.item)).toEqual({
      sessionId: "thread",
      turnId: "turn",
      kind: "output",
      cursor: "turn/item/call_1",
      text: "Cats or dogs?\n- cats\n- dogs",
      data: {
        delivery: "async",
        itemId: "call_1",
        questions: [{ title: "Cats or dogs?", options: ["cats", "dogs"] }],
      },
    });
    // protects=an async message without questions (send_message_to_user_async) stays a plain async output; fails_when=a missing questions field breaks the event; no_claim=none beyond protects
    expect(
      itemEvent("thread", "turn", {
        type: "agentMessage",
        id: "call_2",
        text: "Still working",
        delivery: "async",
      })?.data,
    ).toEqual({ delivery: "async", itemId: "call_2", questions: null });
    // protects=ordinary answers keep a data-free output event; fails_when=a null delivery is reported as async; no_claim=none beyond protects
    expect(
      itemEvent("thread", "turn", {
        type: "agentMessage",
        id: "msg_1",
        text: "Done",
        delivery: null,
      }),
    ).not.toHaveProperty("data");
  });
});
