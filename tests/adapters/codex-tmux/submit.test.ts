import {
  appendFile,
  mkdir,
  mkdtemp,
  readFile,
  rm,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, expect, it, vi } from "vitest";
import { UnsupportedCapabilityError } from "../../../src/adapters/contract.ts";
import {
  CodexTmuxAdapter,
  SubmitUncertainError,
} from "../../../src/adapters/codex-tmux/index.ts";
import { TmuxRunner } from "../../../src/adapters/codex-tmux/runner.ts";

afterEach(() => {
  vi.restoreAllMocks();
});
async function setup() {
  const home = await mkdtemp(join(tmpdir(), "thc-submit-test-"));
  vi.spyOn(TmuxRunner.prototype, "start").mockResolvedValue({
    paneId: "%1",
    pid: "1",
    executable: "/codex",
    startTime: "1",
  });
  vi.spyOn(TmuxRunner.prototype, "run").mockResolvedValue("");
  vi.spyOn(TmuxRunner.prototype, "guardWorkspace").mockResolvedValue();
  const paste = vi.spyOn(TmuxRunner.prototype, "paste").mockResolvedValue();
  const key = vi.spyOn(TmuxRunner.prototype, "key").mockResolvedValue();
  const adapter = new CodexTmuxAdapter({
    cwd: home,
    codexHome: home,
    executable: "/codex",
    sandbox: "read-only",
    persistSubmission: () => Promise.resolve(),
    ackTimeoutMs: 0,
  });
  await adapter.newSession();
  return { adapter, home, paste, key };
}
const screen = async (name: string): Promise<string> =>
  readFile(`tests/fixtures/codex-tmux/pane/${name}.txt`, "utf8");
it("does not persist or paste when the owner composer remains occupied", async () => {
  const { adapter, home, paste, key } = await setup();
  const persist = vi.fn(() => Promise.resolve());
  adapter.options.persistSubmission = persist;
  vi.spyOn(TmuxRunner.prototype, "capture").mockResolvedValue(
    await screen("typed"),
  );
  try {
    await expect(
      adapter.submit({ correlationId: "id", text: "hello" }),
    ).rejects.toThrow("not safely empty");
    expect(persist).not.toHaveBeenCalled();
    expect(paste).not.toHaveBeenCalled();
    expect(key).not.toHaveBeenCalled();
  } finally {
    await adapter.close();
    await rm(home, { recursive: true, force: true });
  }
});
it("refuses foreign composer contents without pressing Enter", async () => {
  const { adapter, home, paste, key } = await setup();
  try {
    const empty = await screen("empty");
    vi.spyOn(TmuxRunner.prototype, "capture")
      .mockResolvedValueOnce(empty)
      .mockResolvedValueOnce(
        empty.replace(
          "Ask Codex to do anything",
          "[Pasted Content 1001 chars]foreign",
        ),
      );
    await expect(
      adapter.submit({ correlationId: "id", text: "hello" }),
    ).rejects.toThrow("does not match");
    expect(key).not.toHaveBeenCalled();
    expect(paste).toHaveBeenCalledTimes(1);
  } finally {
    await adapter.close();
    await rm(home, { recursive: true, force: true });
  }
});
it("never resends an uncertain input and refuses unsupported approval", async () => {
  const { adapter, home, paste, key } = await setup();
  try {
    const empty = await screen("empty");
    vi.spyOn(TmuxRunner.prototype, "capture").mockImplementation(() => {
      const calls = paste.mock.calls;
      const text = calls.at(-1)?.[1];
      if (typeof text !== "string") return Promise.resolve(empty);
      return Promise.resolve(
        empty.replace(
          "Ask Codex to do anything",
          `[Pasted Content ${String(Array.from(text).length)} chars]`,
        ),
      );
    });
    const input = { correlationId: "id", text: "x".repeat(1100) };
    await expect(adapter.submit(input)).rejects.toBeInstanceOf(
      SubmitUncertainError,
    );
    await expect(adapter.submit(input)).rejects.toThrow("previously submitted");
    expect(key).toHaveBeenCalledTimes(1);
    expect(paste).toHaveBeenCalledTimes(1);
    await expect(adapter.approve()).rejects.toBeInstanceOf(
      UnsupportedCapabilityError,
    );
  } finally {
    await adapter.close();
    await rm(home, { recursive: true, force: true });
  }
});

it.each(["busy", "busy-dynamic-header"])(
  "interrupts the identified active turn with %s and awaits an abort record",
  async (pane) => {
    const { adapter, home, key } = await setup();
    adapter.options.ackTimeoutMs = 1000;
    const descriptor = adapter.snapshot();
    const dir = rolloutDirectory(home, descriptor.startedAt);
    const path = join(dir, "rollout.jsonl");
    try {
      await mkdir(dir, { recursive: true });
      await writeStartedRollout(
        join(dir, "unrelated.jsonl"),
        home,
        "another-originator",
      );
      await writeStartedRollout(path, home, descriptor.originator);
      vi.spyOn(TmuxRunner.prototype, "capture").mockResolvedValue(
        await screen(pane),
      );
      key.mockImplementation(async () => {
        await appendFile(
          path,
          JSON.stringify({
            type: "event_msg",
            payload: {
              type: "turn_aborted",
              turn_id: "turn-1",
              reason: "interrupted",
            },
          }) + "\n",
        );
      });
      await expect(adapter.interrupt("other-turn")).rejects.toThrow(
        "not active",
      );
      expect(key).not.toHaveBeenCalled();
      await expect(adapter.interrupt("turn-1")).resolves.toBeUndefined();
      expect(key).toHaveBeenCalledTimes(1);
      expect(key).toHaveBeenCalledWith(descriptor.identity, "Escape");
      expect(await adapter.reconcile()).toContainEqual(
        expect.objectContaining({
          kind: "turn-completed",
          turnId: "turn-1",
          data: { status: "aborted", reason: "interrupted" },
        }),
      );
    } finally {
      await adapter.close();
      await rm(home, { recursive: true, force: true });
    }
  },
);

async function writeStartedRollout(
  path: string,
  cwd: string,
  originator: string,
): Promise<void> {
  const lines = [
    {
      type: "session_meta",
      payload: { id: "thread-1", cwd, source: "cli", originator },
    },
    { type: "event_msg", payload: { type: "task_started", turn_id: "turn-1" } },
  ];
  await writeFile(
    path,
    lines.map((line) => JSON.stringify(line)).join("\n") + "\n",
  );
}

it("persists before paste and acknowledges the exact rollout user item", async () => {
  const { adapter, home, paste, key } = await setup();
  const persisted: string[] = [];
  adapter.options.ackTimeoutMs = 2000;
  adapter.options.pollMs = 1;
  adapter.options.persistSubmission = (input) => {
    persisted.push(input.envelope);
    return Promise.resolve();
  };
  const descriptor = adapter.snapshot();
  const dir = rolloutDirectory(home, descriptor.startedAt);
  const path = join(dir, "rollout.jsonl");
  try {
    await mkdir(dir, { recursive: true });
    await writeStartedRollout(path, home, descriptor.originator);
    const empty = await screen("empty");
    mockPlaceholder(empty, () => paste.mock.calls.at(-1)?.[1]);
    key.mockImplementation(async () => {
      expect(persisted).toHaveLength(1);
      assertPhysicalPaste(paste.mock.calls.at(-1)?.[1], persisted[0]);
      await appendUserItem(path, persisted[0]);
    });
    await expect(
      adapter.submit({
        correlationId: "success",
        text: "first\tvalue  \n\nlast   ",
      }),
    ).resolves.toEqual({});
    expect(persisted[0]).toContain(
      "first\tvalue  \n\nlast   \n</telegram-message>",
    );
    expect(await adapter.reconcile()).toContainEqual(
      expect.objectContaining({
        kind: "input",
        correlationId: "success",
        text: persisted[0],
      }),
    );
    expect(adapter.getThreadIdentity()).toEqual({
      threadId: "thread-1",
      rolloutPath: path,
    });
  } finally {
    await adapter.close();
    await rm(home, { recursive: true, force: true });
  }
});
function mockPlaceholder(empty: string, value: () => string | undefined): void {
  vi.spyOn(TmuxRunner.prototype, "capture").mockImplementation(() => {
    const text = value();
    return Promise.resolve(
      text === undefined
        ? empty
        : empty.replace(
            "Ask Codex to do anything",
            `[Pasted Content ${String(Array.from(text).length)} chars]`,
          ),
    );
  });
}
function assertPhysicalPaste(
  physical: string | undefined,
  saved: string | undefined,
): void {
  if (saved === undefined) throw new Error("Missing persisted envelope");
  expect(physical).toBe(saved + " ".repeat(1001 - Array.from(saved).length));
}

it("refuses an ambiguous busy pane and detects normal completion during interrupt", async () => {
  const { adapter, home, key } = await setup();
  adapter.options.ackTimeoutMs = 1000;
  adapter.options.pollMs = 1;
  const descriptor = adapter.snapshot();
  const dir = rolloutDirectory(home, descriptor.startedAt);
  const path = join(dir, "rollout.jsonl");
  try {
    await mkdir(dir, { recursive: true });
    await writeStartedRollout(path, home, descriptor.originator);
    const capture = vi
      .spyOn(TmuxRunner.prototype, "capture")
      .mockResolvedValue(await screen("modal"));
    await expect(adapter.interrupt("turn-1")).rejects.toThrow(
      "busy pane is ambiguous",
    );
    expect(key).not.toHaveBeenCalled();
    capture.mockResolvedValue(await screen("busy"));
    key.mockImplementation(async () => {
      await appendFile(
        path,
        JSON.stringify({
          type: "event_msg",
          payload: {
            type: "task_complete",
            turn_id: "turn-1",
            last_agent_message: "done",
          },
        }) + "\n",
      );
    });
    await expect(adapter.interrupt("turn-1")).rejects.toThrow(
      "without an interrupt acknowledgement",
    );
  } finally {
    await adapter.close();
    await rm(home, { recursive: true, force: true });
  }
});

async function appendUserItem(
  path: string,
  text: string | undefined,
): Promise<void> {
  await appendFile(
    path,
    JSON.stringify({
      type: "response_item",
      payload: { type: "message", role: "user", content: [{ text }] },
    }) + "\n",
  );
}

function rolloutDirectory(home: string, startedAt: number): string {
  return join(
    home,
    "sessions",
    new Date(startedAt).toISOString().slice(0, 10).replaceAll("-", "/"),
  );
}
