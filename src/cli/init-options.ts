import { readFile, realpath } from "node:fs/promises";
import { isAbsolute, join } from "node:path";
import { readLockOwner } from "../core/lock.js";
import { Store } from "../store/index.js";
import type { Arguments } from "./arguments.js";
import { flag } from "./arguments.js";
import type { CliDependencies } from "./context.js";
import { exists } from "./paths.js";

/** Keep the identity of a legacy symlinked project when it still resolves here. */
export async function configuredDirectory(canonical: string): Promise<string> {
  const database = join(canonical, ".thc", "store.db");
  if (!(await exists(database))) return canonical;
  try {
    const store = new Store(database, true);
    let directory: string | undefined;
    try {
      const id = store.getCursor("entity-id");
      directory = id ? store.getEntity(id)?.directory : undefined;
    } finally {
      store.close();
    }
    if (
      directory &&
      isAbsolute(directory) &&
      (await realpath(directory)) === canonical
    )
      return directory;
  } catch {
    // Missing or unreadable legacy identity is handled by normal initialization checks.
  }
  return canonical;
}
export async function installedTarget(
  args: Arguments,
  directory: string,
  existing: boolean,
): Promise<string | undefined> {
  const target = flag(args, "to");
  if (target || !existing || args.flags.has("update")) return target;
  const path = join(directory, ".thc", "version");
  return (await exists(path))
    ? (await readFile(path, "utf8")).trim()
    : undefined;
}
export async function alreadyRunning(
  args: Arguments,
  directory: string,
  deps: CliDependencies,
  active: (directory: string, deps: CliDependencies) => Promise<boolean>,
): Promise<boolean> {
  if (
    args.flags.has("no-start") ||
    args.flags.has("update") ||
    flag(args, "to") ||
    args.flags.has("force")
  )
    return false;
  return (
    (await active(directory, deps)) || Boolean(await readLockOwner(directory))
  );
}
export function reportCliPath(deps: CliDependencies): void {
  const bin = join(deps.home, ".local", "bin");
  if (!(deps.env.PATH ?? "").split(":").includes(bin))
    deps.stdout(`The CLI is available at ${join(bin, "thc")}.\n`);
}
