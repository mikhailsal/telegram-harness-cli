import { readFile, realpath } from "node:fs/promises";
import { isAbsolute, join, resolve } from "node:path";
import type { Config } from "../core/config.js";
import { readLockOwner } from "../core/lock.js";
import { openStore, readStoreIdentity } from "../store/index.js";
import type { Arguments } from "./arguments.js";
import { flag } from "./arguments.js";
import type { CliDependencies } from "./context.js";
import { CliError } from "./errors.js";
import { sharesSchema } from "./install.js";
import { exists } from "./paths.js";

async function resolvesTo(path: string, target: string): Promise<boolean> {
  try {
    return (await realpath(path)) === target;
  } catch {
    // A folder that is gone or cannot be resolved is not this project.
    return false;
  }
}
/**
 * Keep the identity of a legacy symlinked project when it still resolves here.
 * A store that exists but cannot be read stops setup: guessing would give the
 * project a second service name that its own store then rejects.
 */
export async function configuredDirectory(canonical: string): Promise<string> {
  const database = join(canonical, ".thc", "store.db");
  if (!(await exists(database))) return canonical;
  const saved = readStoreIdentity(database)?.directory;
  if (saved && isAbsolute(saved) && (await resolvesTo(saved, canonical)))
    return saved;
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
function sameTokenFile(
  token: Config["token"],
  path: string,
  directory: string,
): boolean {
  return "file" in token && resolve(directory, token.file) === resolve(path);
}
/**
 * A configured folder keeps its saved settings. An explicit setting that
 * contradicts them is an error rather than a silently ignored flag.
 */
export function assertSettingsMatch(
  args: Arguments,
  saved: Config,
  directory: string,
): void {
  const sandbox = flag(args, "sandbox");
  const userId = flag(args, "user-id");
  const tokenFile = flag(args, "token-file");
  const differing = [
    sandbox !== undefined && sandbox !== saved.sandbox ? "--sandbox" : "",
    userId !== undefined && !saved.allowlist.includes(Number(userId))
      ? "--user-id"
      : "",
    tokenFile !== undefined && !sameTokenFile(saved.token, tokenFile, directory)
      ? "--token-file"
      : "",
  ].filter(Boolean);
  if (differing.length === 0) return;
  throw new CliError(
    "E_CONFIG",
    `${differing.join(", ")} would change the saved configuration of ${directory}`,
    `Edit ${join(directory, ".thc", "config.json")}, then run thc stop and thc init so the bridge restarts with the new settings`,
    3,
  );
}
export function reportCliPath(deps: CliDependencies): void {
  const bin = join(deps.home, ".local", "bin");
  if (!(deps.env.PATH ?? "").split(":").includes(bin))
    deps.stdout(`The CLI is available at ${join(bin, "thc")}.\n`);
}
/**
 * How setup treats the first-answer onboarding. Its cursors live in the
 * project store, which this executable may open only when the selected
 * version uses the same schema. Otherwise opening it would migrate the store
 * past what that version can read, so setup leaves the store alone.
 */
export type Onboarding = "new" | "resume" | "skip";
export async function onboardingFor(
  home: string,
  version: string,
  existing: boolean,
): Promise<Onboarding> {
  if (!(await sharesSchema(home, version))) return "skip";
  return existing ? "resume" : "new";
}
/**
 * A new configuration starts the onboarding conversation. So does a
 * configured folder that never delivered an answer, for example after an
 * earlier run saved the configuration and then failed to start the service.
 */
export function markFirstRun(
  directory: string,
  onboarding: Onboarding,
  start: string,
): boolean {
  if (onboarding === "skip") return false;
  const store = openStore(join(directory, ".thc", "store.db"));
  try {
    const firstRun =
      onboarding === "new" || !store.getCursor("first-delivery-at");
    if (firstRun) {
      store.setCursor("first-delivery-at", "");
      store.setCursor("welcome-pending", start);
    }
    return firstRun;
  } finally {
    store.close();
  }
}
