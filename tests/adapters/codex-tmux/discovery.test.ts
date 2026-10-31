import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, it } from "vitest";
import { rolloutPaths } from "../../../src/adapters/codex-tmux/files.ts";

it("finds a rollout materialized between runner start and a later restore", async () => {
  const root = await mkdtemp(join(tmpdir(), "thc-discovery-"));
  try {
    const startedAt = Date.now() - 6 * 86400000;
    const day = new Date(startedAt + 3 * 86400000)
      .toISOString()
      .slice(0, 10)
      .replaceAll("-", "/");
    const directory = join(root, day);
    await mkdir(directory, { recursive: true });
    const path = join(directory, "rollout.jsonl");
    await writeFile(path, "{}\n");
    expect(await rolloutPaths(root, startedAt)).toContain(path);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
