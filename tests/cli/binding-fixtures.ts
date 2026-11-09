import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Store } from "../../src/store/index.js";

export const token = "12345:secret_token";
const roots: string[] = [];

/** Remove every folder `fixture` created; call it from `afterEach`. */
export async function removeFixtures(): Promise<void> {
  await Promise.all(
    roots.splice(0).map((root) => rm(root, { recursive: true, force: true })),
  );
}
export async function fixture() {
  const home = await mkdtemp(join(tmpdir(), "thc-binding-"));
  roots.push(home);
  const a = join(home, "a");
  const b = join(home, "b");
  await Promise.all([mkdir(a), mkdir(b)]);
  return { home, a, b };
}
export async function configure(directory: string, value: unknown = {}) {
  await mkdir(join(directory, ".thc"), { recursive: true });
  await writeFile(
    join(directory, ".thc", "config.json"),
    JSON.stringify(value),
  );
}
/** Persist the identity a bridge records once it has talked to its bot. */
export function identify(directory: string, botId: string) {
  const store = new Store(join(directory, ".thc", "store.db"));
  store.putEntity({
    id: "entity",
    directory,
    botId,
    createdAt: "2026-01-01",
  });
  store.setCursor("entity-id", "entity");
  store.close();
}
