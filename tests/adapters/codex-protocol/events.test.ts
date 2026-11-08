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
