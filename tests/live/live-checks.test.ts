import { test } from "vitest";
import assert from "node:assert/strict";
import { readProtocol } from "./live-runtime.ts";
import {
  approvalCallback,
  assertDenied,
  assertOrderedAnswers,
  assertProtocolInputs,
  assertTmuxInputs,
  hasToolAttempt,
  hasRunningTool,
  ProtocolCommandMonitor,
} from "./live-checks.ts";

test("protocol correlations reject missing or duplicated input", () => {
  const item = {
    type: "userMessage",
    clientId: "a",
    content: [{ text: "first" }],
  };
  assert.doesNotThrow(() => {
    assertProtocolInputs([item], [{ turnId: "a", marker: "first" }]);
  });
  assert.throws(() => {
    assertProtocolInputs([item, item], [{ turnId: "a", marker: "first" }]);
  });
  assert.throws(() => {
    assertProtocolInputs([], [{ turnId: "a", marker: "first" }]);
  });
});
test("tmux envelopes and ordered answers must each occur exactly once", () => {
  const row = {
    type: "response_item",
    payload: {
      type: "message",
      role: "user",
      content: [{ text: "envelope first" }],
    },
  };
  assert.doesNotThrow(() => {
    assertTmuxInputs(
      [row],
      [{ turnId: "a", marker: "first", envelope: "envelope first" }],
    );
  });
  assert.throws(() => {
    assertTmuxInputs(
      [row, row],
      [{ turnId: "a", marker: "first", envelope: "envelope first" }],
    );
  });
  const messages = [
    { author: "assistant" as const, turnId: "a", text: "first" },
    { author: "assistant" as const, turnId: "b", text: "second" },
  ];
  assert.doesNotThrow(() => {
    assertOrderedAnswers(messages, [
      { turnId: "a", marker: "first" },
      { turnId: "b", marker: "second" },
    ]);
  });
  assert.throws(() => {
    assertOrderedAnswers(messages.reverse(), [
      { turnId: "a", marker: "first" },
      { turnId: "b", marker: "second" },
    ]);
  });
});
test("tool denial and callback evidence cannot be inferred from user text", () => {
  assert.equal(hasToolAttempt([{ command: "sleep 3" }], "sleep 3"), true);
  assert.throws(() => {
    assertDenied([{ command: "write target" }], "target");
  });
  assert.doesNotThrow(() => {
    assertDenied(
      [
        { command: "write target", call_id: "c" },
        { output: "Permission denied", call_id: "c" },
      ],
      "target",
    );
  });
  assert.throws(() => {
    assertDenied(
      [
        { command: "write target", call_id: "c" },
        { output: "Permission denied", call_id: "different" },
      ],
      "target",
    );
  });
  assert.equal(
    approvalCallback(4, 42, "p:request:a").callback_query.message.message_id,
    42,
  );
});

test("restart evidence requires a currently executing command", () => {
  const active = {
    items: [],
    tools: [
      { type: "commandExecution", command: "sleep 3", status: "inProgress" },
    ],
  };
  assert.equal(hasRunningTool(active, "sleep 3"), true);
  assert.equal(
    hasRunningTool(
      { items: [], tools: [{ ...active.tools[0], status: "completed" }] },
      "sleep 3",
    ),
    false,
  );
  const start = {
    payload: { type: "function_call", call_id: "c", arguments: "sleep 3" },
  };
  const end = {
    payload: { type: "function_call_output", call_id: "c", output: "done" },
  };
  assert.equal(hasRunningTool({ tools: [start] }, "sleep 3"), true);
  assert.equal(hasRunningTool({ tools: [start, end] }, "sleep 3"), false);
});

test("running process evidence survives an interim tool response but not a process end", () => {
  const start = {
    payload: { type: "exec_command_begin", call_id: "c", command: "sleep 3" },
  };
  const interim = {
    payload: {
      type: "function_call_output",
      call_id: "c",
      output: "process running",
    },
  };
  const end = {
    payload: { type: "exec_command_end", call_id: "c", exit_code: 0 },
  };
  assert.equal(hasRunningTool({ tools: [start, interim] }, "sleep 3"), true);
  assert.equal(
    hasRunningTool({ tools: [start, interim, end] }, "sleep 3"),
    false,
  );
});

test("protocol command monitor uses only owned started/completed notifications", () => {
  const monitor = new ProtocolCommandMonitor("owned");
  const item = {
    type: "commandExecution",
    id: "command-1",
    status: "inProgress",
    command: "sleep 3",
  };
  monitor.receive({
    method: "item/started",
    params: { threadId: "unrelated", item },
  });
  assert.equal(
    hasRunningTool({ items: [], tools: monitor.active() }, "sleep 3"),
    false,
  );
  monitor.receive({
    method: "item/started",
    params: { threadId: "owned", item },
  });
  assert.equal(hasToolAttempt(monitor.active(), "sleep 3"), true);
  assert.equal(
    hasRunningTool({ items: [], tools: monitor.active() }, "sleep 3"),
    true,
  );
  monitor.receive({
    method: "item/completed",
    params: { threadId: "owned", item: { ...item, status: "completed" } },
  });
  assert.equal(
    hasRunningTool({ items: [], tools: monitor.active() }, "sleep 3"),
    false,
  );
});

test("a temporarily unavailable turn list does not masquerade as history evidence", async () => {
  const monitor = new ProtocolCommandMonitor("owned");
  monitor.receive({
    method: "item/started",
    params: {
      threadId: "owned",
      item: {
        type: "commandExecution",
        id: "c",
        status: "inProgress",
        command: "sleep 3",
      },
    },
  });
  const transient = {
    call: () => Promise.reject(new Error("list_turns is not supported yet")),
  };
  const snapshot = await readProtocol(transient, "owned", monitor);
  assert.equal(hasRunningTool(snapshot, "sleep 3"), true);
  assert.throws(() => {
    assertProtocolInputs(snapshot.items ?? [], [
      { turnId: "x", marker: "task" },
    ]);
  });
  await assert.rejects(
    readProtocol(
      { call: () => Promise.reject(new Error("disconnected")) },
      "owned",
      monitor,
    ),
    /disconnected/,
  );
});
