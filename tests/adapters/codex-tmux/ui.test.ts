import { readFile } from "node:fs/promises";
import { afterEach, expect, it, vi } from "vitest";
import {
  TmuxRunner,
  type PaneIdentity,
} from "../../../src/adapters/codex-tmux/runner.ts";
import { waitForEmptyComposer } from "../../../src/adapters/codex-tmux/ui.ts";

const identity: PaneIdentity = {
  paneId: "%1",
  pid: "1",
  executable: "/codex",
  startTime: "1",
};
afterEach(() => vi.restoreAllMocks());
async function pane(name: string): Promise<string> {
  return readFile(`tests/fixtures/codex-tmux/pane/${name}.txt`, "utf8");
}
it.each(["blank", "busy"] as const)(
  "waits for %s startup until the owned composer is empty",
  async (state) => {
    const runner = new TmuxRunner("owned");
    const guard = vi.spyOn(runner, "guardWorkspace").mockResolvedValue();
    const capture = vi
      .spyOn(runner, "capture")
      .mockResolvedValueOnce(state === "blank" ? "" : await pane("busy"))
      .mockResolvedValueOnce(await pane("empty"));
    await expect(
      waitForEmptyComposer(runner, identity, "/owned", 100, 1),
    ).resolves.toBeUndefined();
    expect(capture).toHaveBeenCalledTimes(2);
    expect(guard).toHaveBeenCalledTimes(2);
  },
);
it("leaves an occupied composer untouched and times out", async () => {
  const runner = new TmuxRunner("owned");
  vi.spyOn(runner, "guardWorkspace").mockResolvedValue();
  vi.spyOn(runner, "capture").mockResolvedValue(await pane("typed"));
  const paste = vi.spyOn(runner, "paste"),
    key = vi.spyOn(runner, "key");
  await expect(
    waitForEmptyComposer(runner, identity, "/owned", 10, 1),
  ).rejects.toThrow("not safely empty");
  expect(paste).not.toHaveBeenCalled();
  expect(key).not.toHaveBeenCalled();
});
it("stops immediately when the pane identity or workspace changes", async () => {
  const runner = new TmuxRunner("owned");
  vi.spyOn(runner, "guardWorkspace").mockRejectedValue(
    new Error("identity changed"),
  );
  const capture = vi.spyOn(runner, "capture");
  await expect(
    waitForEmptyComposer(runner, identity, "/owned", 100, 1),
  ).rejects.toThrow("identity changed");
  expect(capture).not.toHaveBeenCalled();
});
