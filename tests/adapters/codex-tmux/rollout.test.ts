import { appendFile, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, it } from "vitest";
import {
  readRollout,
  rolloutEvent,
} from "../../../src/adapters/codex-tmux/rollout.ts";
import { messageEnvelope } from "../../../src/adapters/codex-tmux/index.ts";

it("tails only complete UTF-8 records with byte cursors", async () => {
  const dir = await mkdtemp(join(tmpdir(), "thc-rollout-test-"));
  const path = join(dir, "rollout.jsonl");
  try {
    const text = messageEnvelope(
      "\n  Привет 🌍  \n",
      new Date("2026-10-31T12:34:56Z"),
    );
    const line = JSON.stringify({
      type: "response_item",
      payload: { type: "message", role: "user", content: [{ text }] },
    });
    await writeFile(path, line);
    expect((await readRollout(path, "s", 0)).offset).toBe(0);
    await appendFile(path, "\n");
    const first = await readRollout(path, "s", 0);
    expect(first.offset).toBe(Buffer.byteLength(line) + 1);
    expect(first.events).toMatchObject([{ kind: "input", text }]);
    expect((await readRollout(path, "s", first.offset)).events).toEqual([]);
    expect((await readFile(path)).length).toBe(first.offset);
    await writeFile(path, "");
    await expect(readRollout(path, "s", first.offset)).rejects.toThrow(
      "truncated",
    );
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});
it("ignores metadata-tagged Codex setup while preserving genuine user text", () => {
  const input = (content: { text: string }[], kinds: string[]) =>
    JSON.stringify({
      type: "response_item",
      payload: {
        type: "message",
        role: "user",
        content,
        internal_chat_message_metadata_passthrough: {
          content_item_kinds: kinds,
        },
      },
    });
  const instructions =
    "# AGENTS.md instructions\n\n<INSTRUCTIONS>synthetic</INSTRUCTIONS>";
  const environment = "<environment_context>synthetic</environment_context>";
  expect(
    rolloutEvent(
      input(
        [{ text: instructions }, { text: environment }],
        ["agents_md.instructions", "environments.environment_context"],
      ),
      "s",
      "1",
    ),
  ).toBeUndefined();
  expect(
    rolloutEvent(input([{ text: instructions }], ["user.text"]), "s", "2"),
  ).toMatchObject({ kind: "input", text: instructions });
  expect(
    rolloutEvent(input([{ text: environment }], ["user.text"]), "s", "3"),
  ).toMatchObject({ kind: "input", text: environment });
  expect(
    rolloutEvent(
      input(
        [{ text: instructions }, { text: "actual" }],
        ["agents_md.instructions", "user.text"],
      ),
      "s",
      "4",
    ),
  ).toMatchObject({ kind: "input", text: instructions + "actual" });
  expect(
    rolloutEvent(input([{ text: instructions }], ["future.unknown"]), "s", "5"),
  ).toMatchObject({ kind: "input", text: instructions });
});
it("reports a request_user_input_async question as a marked output", () => {
  // Shaped after a Codex 0.171.0 rollout record.
  const line = (item: Record<string, unknown>) =>
    JSON.stringify({
      type: "event_msg",
      payload: { type: "item_completed", turn_id: "turn", item },
    });
  const text = "Cats or dogs?\n- cats\n- dogs";
  // protects=the tmux adapter forwards the async question mid-turn; fails_when=only task_complete's last message is read; no_claim=how the engine delivers it
  expect(
    rolloutEvent(
      line({
        type: "AgentMessage",
        id: "call_1",
        content: [{ type: "Text", text }],
        delivery: "async",
      }),
      "s",
      "10",
    ),
  ).toEqual({
    kind: "output",
    sessionId: "s",
    cursor: "10",
    turnId: "turn",
    text,
    data: { delivery: "async" },
  });
  // protects=ordinary agent messages still wait for task_complete; fails_when=every message is forwarded mid-turn; no_claim=none beyond protects
  expect(
    rolloutEvent(
      line({ type: "AgentMessage", content: [{ type: "Text", text: "Hi" }] }),
      "s",
      "11",
    ),
  ).toBeUndefined();
  expect(
    rolloutEvent(
      line({ type: "AgentMessage", content: [], delivery: "async" }),
      "s",
      "12",
    ),
  ).toBeUndefined();
});
