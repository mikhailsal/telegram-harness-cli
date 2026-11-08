import { existsSync } from "node:fs";
import { chmod, mkdir, mkdtemp, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { backup, DatabaseSync } from "node:sqlite";
import { TmuxRunner } from "../../src/adapters/codex-tmux/runner.ts";
import { inspect } from "./live-runtime.ts";
import type { Resources } from "./live-setup.ts";

async function saveFile(path: string, text: string): Promise<void> {
  await writeFile(path, text, { mode: 0o600, flag: "wx" });
}
async function saveDatabase(root: string, target: string): Promise<void> {
  const source = join(root, ".thc", "store.db");
  if (!existsSync(source)) return;
  await saveFile(target, "");
  const db = new DatabaseSync(source, { readOnly: true });
  try {
    await backup(db, target);
  } finally {
    db.close();
  }
  await chmod(target, 0o600);
}
async function savePanes(
  resources: Resources,
  directory: string,
): Promise<void> {
  for (const [name, adapter] of resources.owned) {
    if (!/^thc-[0-9a-f-]{36}$/u.test(name))
      throw new Error("Invalid owned tmux server");
    const descriptor = adapter.snapshot();
    if (descriptor.cwd !== resources.root)
      throw new Error("Owned tmux workspace mismatch");
    const runner = new TmuxRunner(name);
    await runner.guardWorkspace(descriptor.identity, descriptor.cwd);
    const pane = await runner.capture(descriptor.identity);
    await saveFile(join(directory, `${name}.pane.txt`), pane);
  }
}
export async function saveFailure(
  resources: Resources,
  error: unknown,
  base = join(process.cwd(), ".quality", "live-failures"),
): Promise<string> {
  await mkdir(base, { recursive: true, mode: 0o700 });
  const directory = await mkdtemp(join(base, "failure-"));
  await chmod(directory, 0o700);
  await saveFile(
    join(directory, "error.log"),
    `${error instanceof Error ? (error.stack ?? error.message) : String(error)}\n`,
  );
  await saveDatabase(resources.root, join(directory, "store.db"));
  const state = inspect(resources.root);
  if (state)
    await saveFile(
      join(directory, "snapshot.json"),
      `${JSON.stringify(state, null, 2)}\n`,
    );
  await savePanes(resources, directory);
  return directory;
}
