import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, expect, it, vi } from "vitest";
import { CodexTmuxAdapter } from "../../../src/adapters/codex-tmux/index.ts";
import { TmuxRunner } from "../../../src/adapters/codex-tmux/runner.ts";
import * as restore from "../../../src/adapters/codex-tmux/restore.ts";

afterEach(() => {
  vi.restoreAllMocks();
});
it("restores a validated descriptor and never resubmits a persisted envelope", async () => {
  const home = await mkdtemp(join(tmpdir(), "thc-lifecycle-"));
  vi.spyOn(TmuxRunner.prototype, "start").mockResolvedValue({
    paneId: "%1",
    pid: "42",
    executable: "/codex",
    startTime: "1",
  });
  const guard = vi.spyOn(TmuxRunner.prototype, "guard").mockResolvedValue();
  const run = vi.spyOn(TmuxRunner.prototype, "run").mockResolvedValue("");
  const validate = vi.spyOn(restore, "validateDescriptor").mockResolvedValue();
  const options = {
    cwd: home,
    codexHome: home,
    executable: "/codex",
    sandbox: "read-only" as const,
    persistSubmission: () => Promise.resolve(),
  };
  const first = new CodexTmuxAdapter(options);
  try {
    await first.newSession();
    const descriptor = first.snapshot();
    await first.close();
    const resumed = await CodexTmuxAdapter.restore(options, descriptor, [
      { correlationId: "pending", envelope: "previously sent" },
    ]);
    expect(validate).toHaveBeenCalled();
    expect(resumed.snapshot()).toEqual(descriptor);
    await expect(
      resumed.submit({ correlationId: "pending", text: "task" }),
    ).rejects.toThrow("previously submitted");
    expect(await resumed.attach()).toEqual({
      command: "tmux",
      args: ["-L", descriptor.server, "attach-session", "-t", "codex"],
    });
    await resumed.terminateSession();
    expect(guard).toHaveBeenCalled();
    expect(run).toHaveBeenCalledWith("kill-server");
    await expect(resumed.terminateSession()).rejects.toThrow("No owned");
  } finally {
    await first.close();
    await rm(home, { recursive: true, force: true });
  }
});
