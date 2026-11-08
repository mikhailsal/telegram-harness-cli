import { appendFile, mkdir, readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { vi } from "vitest";
import { CodexTmuxAdapter } from "../../src/adapters/codex-tmux/index.ts";
import { TmuxRunner } from "../../src/adapters/codex-tmux/runner.ts";

export async function tmuxStream(root: string) {
  mockRunnerStart();
  vi.spyOn(TmuxRunner.prototype, "guardWorkspace").mockResolvedValue();
  vi.spyOn(TmuxRunner.prototype, "run").mockResolvedValue("");
  const paste = vi.spyOn(TmuxRunner.prototype, "paste").mockResolvedValue();
  let envelope = "";
  const adapter = new CodexTmuxAdapter({
    cwd: root,
    codexHome: root,
    executable: "/codex",
    sandbox: "read-only",
    ackTimeoutMs: 1000,
    pollMs: 1,
    persistSubmission: (input) => {
      envelope = input.envelope;
      return Promise.resolve();
    },
  });
  const session = await adapter.newSession();
  const path = await createRollout(adapter, root);
  const empty = await readFile(
    "tests/fixtures/codex-tmux/pane/empty.txt",
    "utf8",
  );
  const completed = await readFile(
    "tests/fixtures/codex-tmux/pane/completed-history.txt",
    "utf8",
  );
  vi.spyOn(TmuxRunner.prototype, "capture").mockImplementation(() => {
    const physical = paste.mock.calls.at(-1)?.[1];
    const current = key.mock.calls.length ? completed : empty;
    return Promise.resolve(
      physical && paste.mock.calls.length > key.mock.calls.length
        ? current.replace(
            "Ask Codex to do anything",
            `[Pasted Content ${String(Array.from(physical).length)} chars]`,
          )
        : current,
    );
  });
  const key = vi
    .spyOn(TmuxRunner.prototype, "key")
    .mockImplementation(async () => {
      const id = `turn-${String(key.mock.calls.length)}`;
      await appendFile(path, startRecords(id, envelope));
    });
  return { adapter, session, path, paste };
}
function mockRunnerStart(): void {
  vi.spyOn(TmuxRunner.prototype, "start").mockResolvedValue({
    paneId: "%1",
    pid: "1",
    executable: "/codex",
    startTime: "1",
  });
}
async function createRollout(
  adapter: CodexTmuxAdapter,
  root: string,
): Promise<string> {
  const descriptor = adapter.snapshot();
  const day = new Date(descriptor.startedAt)
    .toISOString()
    .slice(0, 10)
    .replaceAll("-", "/");
  const dir = join(root, "sessions", day),
    path = join(dir, "rollout.jsonl");
  await mkdir(dir, { recursive: true });
  await writeFile(
    path,
    line({
      type: "session_meta",
      payload: {
        id: "thread-1",
        cwd: root,
        source: "cli",
        originator: descriptor.originator,
      },
    }),
  );
  return path;
}
function line(value: unknown): string {
  return `${JSON.stringify(value)}\n`;
}
function startRecords(turnId: string, envelope: string): string {
  return [
    { type: "event_msg", payload: { type: "task_started", turn_id: turnId } },
    {
      type: "response_item",
      payload: {
        type: "message",
        role: "user",
        content: [
          {
            text: "# AGENTS.md instructions\n\n<INSTRUCTIONS>fixture</INSTRUCTIONS>",
          },
          { text: "<environment_context>fixture</environment_context>" },
        ],
        internal_chat_message_metadata_passthrough: {
          content_item_kinds: [
            "agents_md.instructions",
            "environments.environment_context",
          ],
        },
      },
    },
    {
      type: "response_item",
      payload: {
        type: "message",
        role: "user",
        content: [{ text: envelope }],
        internal_chat_message_metadata_passthrough: {
          content_item_kinds: ["user.text"],
        },
      },
    },
  ]
    .map(line)
    .join("");
}
export async function finishTmuxTurn(path: string): Promise<void> {
  await appendFile(
    path,
    line({
      type: "event_msg",
      payload: {
        type: "task_complete",
        turn_id: "turn-1",
        last_agent_message: "exact-marker",
      },
    }),
  );
}
