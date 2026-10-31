// These tests replace OS and tmux boundaries; live probes are separate evidence.
import { EventEmitter } from "node:events";
import { beforeEach, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({
  calls: [] as string[][],
  result: "%1 42\n",
  executable: "/bin/codex",
  stat: `42 (codex) ${Array(19).fill("0").join(" ")} 100`,
  exit: 0,
  input: "",
  failure: false,
}));
vi.mock("node:util", async (importOriginal) => {
  const original = await importOriginal<typeof import("node:util")>();
  return {
    ...original,
    promisify: () => (_command: string, args: string[]) => {
      state.calls.push(args);
      return state.failure
        ? Promise.reject(new Error("tmux failed"))
        : Promise.resolve({ stdout: state.result });
    },
  };
});
vi.mock("node:child_process", () => ({
  execFile: (
    _command: string,
    args: string[],
    _options: unknown,
    cb: (error: Error | null, stdout: string, stderr: string) => void,
  ) => {
    state.calls.push(args);
    cb(state.failure ? new Error("tmux failed") : null, state.result, "");
  },
  spawn: (_command: string, args: string[]) => {
    state.calls.push(args);
    const child = new EventEmitter() as EventEmitter & {
      stdin: { end: (text: string) => void };
    };
    child.stdin = {
      end: (text) => {
        state.input = text;
        queueMicrotask(() => child.emit("close", state.exit));
      },
    };
    return child;
  },
}));
vi.mock("node:fs/promises", () => ({
  realpath: (path: string) =>
    Promise.resolve(path.startsWith("/proc/") ? state.executable : path),
  readFile: () => Promise.resolve(state.stat),
}));
import { TmuxRunner } from "../../../src/adapters/codex-tmux/runner.ts";
const identity = {
  paneId: "%1",
  pid: "42",
  executable: "/bin/codex",
  startTime: "100",
};
beforeEach(() => {
  state.calls = [];
  state.result = "%1 42\n";
  state.executable = "/bin/codex";
  state.stat = `42 (codex) ${Array(19).fill("0").join(" ")} 100`;
  state.exit = 0;
  state.input = "";
  state.failure = false;
});
it("starts Codex as a dedicated pane with explicit sandbox and originator", async () => {
  const runner = new TmuxRunner("thc-owned");
  expect(
    await runner.start(
      "/work",
      "/home",
      "/bin/codex",
      "read-only",
      "thc-marker",
    ),
  ).toEqual(identity);
  expect(state.calls[0]).toEqual([
    "-L",
    "thc-owned",
    "new-session",
    "-d",
    "-s",
    "codex",
    "-x",
    "160",
    "-y",
    "40",
    "-c",
    "/work",
    "-e",
    "CODEX_HOME=/home",
    "-e",
    "CODEX_INTERNAL_ORIGINATOR_OVERRIDE=thc-marker",
    "/bin/codex",
    "--no-daemon",
    "-a",
    "never",
    "-s",
    "read-only",
  ]);
});
it("refuses a replaced pane, executable, and reused pid before input", async () => {
  const runner = new TmuxRunner("owned");
  state.result = "%1 43";
  await expect(runner.key(identity, "Enter")).rejects.toThrow(
    "identity changed",
  );
  state.result = "%1 42";
  state.executable = "/bin/other";
  await expect(runner.capture(identity)).rejects.toThrow("identity changed");
  state.executable = "/bin/codex";
  state.stat = `42 (codex) ${Array(19).fill("0").join(" ")} 101`;
  await expect(runner.paste(identity, "hello")).rejects.toThrow(
    "identity changed",
  );
  expect(
    state.calls.every(
      (call) => !call.includes("send-keys") && !call.includes("paste-buffer"),
    ),
  ).toBe(true);
});
it("loads bracketed paste via stdin and checks tmux exit status", async () => {
  const runner = new TmuxRunner("owned");
  await runner.paste(identity, "first\nsecond");
  expect(state.input).toBe("first\nsecond");
  expect(state.calls).toContainEqual([
    "-L",
    "owned",
    "paste-buffer",
    "-d",
    "-p",
    "-t",
    "%1",
  ]);
  state.exit = 2;
  await expect(runner.load("data")).rejects.toThrow("exited 2");
  state.failure = true;
  await expect(runner.run("capture-pane")).rejects.toThrow("tmux failed");
});
