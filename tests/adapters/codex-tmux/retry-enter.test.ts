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
import {
  CodexTmuxAdapter,
  SubmitUncertainError,
} from "../../../src/adapters/codex-tmux/index.ts";
import { TmuxRunner } from "../../../src/adapters/codex-tmux/runner.ts";
afterEach(() => vi.restoreAllMocks());
type Scenario = "dropped" | "empty" | "busy" | "edited" | "late";
async function setupAdapter(home: string) {
  vi.spyOn(TmuxRunner.prototype, "start").mockResolvedValue({
    paneId: "%1",
    pid: "1",
    executable: "/codex",
    startTime: "1",
  });
  vi.spyOn(TmuxRunner.prototype, "guardWorkspace").mockResolvedValue();
  vi.spyOn(TmuxRunner.prototype, "run").mockResolvedValue("");
  const paste = vi.spyOn(TmuxRunner.prototype, "paste").mockResolvedValue();
  const key = vi.spyOn(TmuxRunner.prototype, "key");
  const adapter = new CodexTmuxAdapter({
    cwd: home,
    codexHome: home,
    executable: "/codex",
    sandbox: "read-only",
    ackTimeoutMs: 850,
    pollMs: 20,
    persistSubmission: () => Promise.resolve(),
  });
  await adapter.newSession();
  return { adapter, paste, key };
}
async function makeRollout(
  home: string,
  adapter: CodexTmuxAdapter,
): Promise<string> {
  const desc = adapter.snapshot();
  const day = new Date(desc.startedAt)
    .toISOString()
    .slice(0, 10)
    .replaceAll("-", "/");
  const dir = join(home, "sessions", day),
    path = join(dir, "rollout.jsonl");
  await mkdir(dir, { recursive: true });
  await writeFile(
    path,
    JSON.stringify({
      type: "session_meta",
      payload: {
        id: "thread-1",
        cwd: home,
        source: "cli",
        originator: desc.originator,
      },
    }) + "\n",
  );
  return path;
}
async function installPane(
  scenario: Scenario,
  text: () => string,
  record: () => Promise<void>,
): Promise<void> {
  const empty = await readFile(
    "tests/fixtures/codex-tmux/pane/empty.txt",
    "utf8",
  );
  const busy = await readFile(
    "tests/fixtures/codex-tmux/pane/busy.txt",
    "utf8",
  );
  let captures = 0;
  vi.spyOn(TmuxRunner.prototype, "capture").mockImplementation(async () => {
    captures++;
    if (captures === 1) return empty;
    const placeholder = empty.replace(
      "Ask Codex to do anything",
      `[Pasted Content ${String(Array.from(text()).length)} chars]`,
    );
    if (captures === 2 || scenario === "dropped") return placeholder;
    if (scenario === "late") {
      await record();
      return placeholder;
    }
    if (scenario === "busy") return busy;
    if (scenario === "edited")
      return placeholder.replace(" chars]", " chars]foreign");
    return empty;
  });
}
async function runScenario(scenario: Scenario) {
  const home = await mkdtemp(join(tmpdir(), "thc-retry-enter-"));
  const { adapter, paste, key } = await setupAdapter(home);
  const path = await makeRollout(home, adapter);
  let envelope = "",
    writes = 0;
  adapter.options.persistSubmission = (input) => {
    envelope = input.envelope;
    return Promise.resolve();
  };
  const record = async () => {
    writes++;
    await appendFile(
      path,
      JSON.stringify({
        type: "response_item",
        payload: {
          type: "message",
          role: "user",
          content: [{ text: envelope }],
        },
      }) + "\n",
    );
  };
  await installPane(scenario, () => paste.mock.calls.at(-1)?.[1] ?? "", record);
  key.mockImplementation(async () => {
    if (scenario === "dropped" && key.mock.calls.length === 2) await record();
  });
  try {
    const submission = adapter.submit({ correlationId: "id", text: "hello" });
    if (scenario === "dropped" || scenario === "late")
      await expect(submission).resolves.toEqual({});
    else await expect(submission).rejects.toBeInstanceOf(SubmitUncertainError);
    expect(paste).toHaveBeenCalledTimes(1);
    expect(key).toHaveBeenCalledTimes(scenario === "dropped" ? 2 : 1);
    expect(writes).toBe(scenario === "dropped" || scenario === "late" ? 1 : 0);
  } finally {
    await adapter.close();
    await rm(home, { recursive: true, force: true });
  }
}
it.each(["dropped", "empty", "busy", "edited", "late"] as const)(
  "retries Enter safely when the composer is %s",
  async (scenario) => {
    await runScenario(scenario);
  },
);
