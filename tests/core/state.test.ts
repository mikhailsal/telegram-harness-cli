import { mkdtemp, readFile, rm, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, it } from "vitest";
import { z } from "zod";
import { StateFile, atomicJson, readJson } from "../../src/core/state.ts";
it("serializes concurrent updates, recovers after failed mutation, and persists privately", async () => {
  const root = await mkdtemp(join(tmpdir(), "thc-state-"));
  const path = join(root, "nested", "state.json");
  try {
    const state = new StateFile(
      path,
      z.object({ count: z.number().int().nonnegative() }),
      { count: 0 },
    );
    expect(await state.read()).toEqual({ count: 0 });
    await Promise.all(
      Array.from({ length: 25 }, () =>
        state.update((current) => ({ count: current.count + 1 })),
      ),
    );
    await expect(state.update(() => ({ count: -1 }))).rejects.toThrow();
    expect(
      await state.update((current) => ({ count: current.count + 1 })),
    ).toEqual({ count: 26 });
    expect(JSON.parse(await readFile(path, "utf8"))).toEqual({ count: 26 });
    expect((await stat(path)).mode & 0o777).toBe(0o600);
    await writeFile(path, "{broken");
    await expect(state.read()).rejects.toThrow();
    await atomicJson(path, { count: "invalid" });
    await expect(
      readJson(path, z.object({ count: z.number() }), { count: 0 }),
    ).rejects.toThrow();
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
