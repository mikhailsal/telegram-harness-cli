// These tests replace OS and tmux boundaries; live probes are separate evidence.
import { beforeEach, expect, it, vi } from "vitest";
import type {
  TmuxDescriptor,
  TmuxOptions,
} from "../../../src/adapters/codex-tmux/index.ts";
import type { TmuxRunner } from "../../../src/adapters/codex-tmux/runner.ts";
const state = vi.hoisted(() => ({
  procCwd: "/work",
  environment: "",
  thread: "thread",
  guard: vi.fn(),
  paths: new Map<string, string>(),
}));
vi.mock("node:fs/promises", () => ({
  readFile: () => Promise.resolve(state.environment),
  realpath: (path: string) =>
    Promise.resolve(
      path === "/proc/42/cwd" ? state.procCwd : (state.paths.get(path) ?? path),
    ),
}));
vi.mock("../../../src/adapters/codex-tmux/files.ts", () => ({
  rolloutIdentity: () => Promise.resolve(state.thread),
}));
import { validateDescriptor } from "../../../src/adapters/codex-tmux/restore.ts";
const originator = "thc-12345678-1234-1234-1234-123456789abc";
const options: TmuxOptions = {
  cwd: "/work",
  codexHome: "/home/codex",
  executable: "/bin/codex",
  sandbox: "read-only",
  persistSubmission: async () => {
    await Promise.resolve();
  },
};
const descriptor: TmuxDescriptor = {
  server: "thc-12345678-1234-1234-1234-123456789abc",
  localId: "local",
  identity: {
    paneId: "%1",
    pid: "42",
    executable: "/bin/codex",
    startTime: "100",
  },
  cwd: "/work",
  threadId: "thread",
  rolloutPath: "/home/codex/sessions/rollout.jsonl",
  cursor: 50,
  startedAt: 123,
  originator,
};
const runner = { guard: state.guard } as unknown as TmuxRunner;
beforeEach(() => {
  state.procCwd = "/work";
  state.environment = `CODEX_HOME=/home/codex\0CODEX_INTERNAL_ORIGINATOR_OVERRIDE=${originator}\0`;
  state.thread = "thread";
  state.paths.clear();
  state.guard.mockReset();
  state.guard.mockResolvedValue(undefined);
});
it("accepts a validated owned process and matching rollout", async () => {
  await expect(
    validateDescriptor(options, descriptor, runner),
  ).resolves.toBeUndefined();
  expect(state.guard).toHaveBeenCalledWith(descriptor.identity);
  await expect(
    validateDescriptor(
      options,
      { ...descriptor, rolloutPath: "", threadId: "" },
      runner,
    ),
  ).resolves.toBeUndefined();
});
it("rejects invalid descriptors and changed process identity", async () => {
  await expect(
    validateDescriptor(options, { ...descriptor, cursor: -1 }, runner),
  ).rejects.toThrow("Invalid session descriptor");
  await expect(
    validateDescriptor(options, { ...descriptor, cwd: "/elsewhere" }, runner),
  ).rejects.toThrow("Invalid session descriptor");
  state.guard.mockRejectedValueOnce(new Error("pane replaced"));
  await expect(validateDescriptor(options, descriptor, runner)).rejects.toThrow(
    "pane replaced",
  );
  state.procCwd = "/other";
  await expect(validateDescriptor(options, descriptor, runner)).rejects.toThrow(
    "directory changed",
  );
});
it("rejects wrong originator, home, rollout location and thread", async () => {
  state.environment = "CODEX_HOME=/home/codex\0";
  await expect(validateDescriptor(options, descriptor, runner)).rejects.toThrow(
    "originator mismatch",
  );
  state.environment = `CODEX_INTERNAL_ORIGINATOR_OVERRIDE=${originator}\0`;
  await expect(validateDescriptor(options, descriptor, runner)).rejects.toThrow(
    "home mismatch",
  );
  state.environment += "CODEX_HOME=/home/codex\0";
  await expect(
    validateDescriptor(
      options,
      { ...descriptor, rolloutPath: "/other/rollout.jsonl" },
      runner,
    ),
  ).rejects.toThrow("rollout identity mismatch");
  state.thread = "different";
  await expect(validateDescriptor(options, descriptor, runner)).rejects.toThrow(
    "rollout identity mismatch",
  );
});
