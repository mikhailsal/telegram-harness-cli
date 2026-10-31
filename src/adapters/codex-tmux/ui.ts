import { parsePane } from "./pane.ts";
import type { PaneIdentity, TmuxRunner } from "./runner.ts";

export async function waitForEmptyComposer(
  runner: TmuxRunner,
  identity: PaneIdentity,
  cwd: string,
  timeoutMs: number,
  pollMs: number,
): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  do {
    await runner.guardWorkspace(identity, cwd);
    if (parsePane(await runner.capture(identity)).kind === "empty") return;
    const remaining = deadline - Date.now();
    if (remaining <= 0) break;
    await new Promise((resolve) =>
      setTimeout(resolve, Math.min(pollMs, remaining)),
    );
  } while (Date.now() <= deadline);
  throw new Error("Codex composer is not safely empty");
}
