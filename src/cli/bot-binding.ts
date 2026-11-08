import { readFile, realpath, unlink } from "node:fs/promises";
import { join, resolve } from "node:path";
import { resolveToken, tokenSchema } from "../core/config.js";
import { atomicJson, isMissing } from "../core/state.js";
import { Store } from "../store/index.js";
import { CliError } from "./errors.js";
import { withSetupLock } from "./setup-lock.js";

async function canonical(path: string): Promise<string> {
  try {
    return await realpath(path);
  } catch (error) {
    if (isMissing(error)) return resolve(path);
    throw error;
  }
}
async function readOptional(path: string): Promise<unknown> {
  try {
    return JSON.parse(await readFile(path, "utf8")) as unknown;
  } catch (error) {
    if (isMissing(error)) return undefined;
    throw error;
  }
}
async function configured(path: string): Promise<boolean> {
  try {
    await readFile(join(path, ".thc", "config.json"));
    return true;
  } catch (error) {
    if (isMissing(error)) return false;
    return unidentified(path);
  }
}
function conflict(path: string): never {
  throw new CliError(
    "E_CONFIG",
    `This bot is already configured for ${path}`,
    `Use the existing project at ${path} or create a different bot with BotFather`,
    3,
  );
}
function unidentified(path: string): never {
  throw new CliError(
    "E_CONFIG",
    `Cannot identify the bot configured for ${path}`,
    `Inspect the configuration and credentials in ${path}, then retry`,
    3,
  );
}
function storedBot(path: string): string | undefined {
  const database = join(path, ".thc", "store.db");
  try {
    const store = new Store(database, true);
    try {
      const entity = store.getCursor("entity-id");
      const id = entity ? store.getEntity(entity)?.botId : undefined;
      return id && /^\d+$/.test(id) ? id : undefined;
    } finally {
      store.close();
    }
  } catch {
    // An absent, old, or unreadable store cannot establish ownership.
    return undefined;
  }
}
async function configToken(path: string) {
  let config: unknown;
  try {
    config = await readOptional(join(path, ".thc", "config.json"));
  } catch {
    return unidentified(path);
  }
  const parsed =
    typeof config === "object" && config !== null && "token" in config
      ? tokenSchema.safeParse(config.token)
      : undefined;
  if (!parsed?.success) return unidentified(path);
  return parsed.data;
}
async function ownerBot(
  path: string,
  env: NodeJS.ProcessEnv,
  candidate: string,
): Promise<string | undefined> {
  if (!(await configured(path))) return undefined;
  const token = await configToken(path);
  const saved = storedBot(path);
  if (saved) return saved;
  let value: string;
  try {
    value = await resolveToken(token, path, env);
  } catch {
    return unidentified(path);
  }
  const id = value.slice(0, value.indexOf(":"));
  // A caller's environment may differ from the one used by the service.
  if ("env" in token && id !== candidate) return unidentified(path);
  return id;
}
async function legacyOwner(
  home: string,
  id: string,
  directory: string,
  env: NodeJS.ProcessEnv,
): Promise<string | undefined> {
  const folders = await readOptional(
    join(home, ".local", "share", "thc", "folders"),
  );
  if (typeof folders !== "object" || folders === null || Array.isArray(folders))
    return;
  for (const path of Object.keys(folders)) {
    const owner = await canonical(path);
    if (owner === directory) continue;
    if ((await ownerBot(owner, env, id)) === id) return owner;
  }
  return undefined;
}

function invalidBinding(path: string): never {
  throw new CliError(
    "E_CONFIG",
    "Invalid bot binding metadata",
    `Inspect the binding metadata at ${path} and retry`,
    3,
  );
}
async function bindingOwner(path: string): Promise<string | undefined> {
  let record: unknown;
  try {
    record = await readOptional(path);
  } catch (error) {
    if (!(error instanceof SyntaxError)) throw error;
    return invalidBinding(path);
  }
  if (record === undefined) return undefined;
  if (
    typeof record !== "object" ||
    record === null ||
    !("directory" in record) ||
    typeof record.directory !== "string"
  )
    return invalidBinding(path);
  return canonical(record.directory);
}
async function assertAvailable(
  home: string,
  path: string,
  project: string,
  id: string,
  env: NodeJS.ProcessEnv,
): Promise<void> {
  const owner = await bindingOwner(path);
  if (owner && owner !== project && (await configured(owner))) {
    const existingId = await ownerBot(owner, env, id);
    if (!existingId || existingId === id) conflict(owner);
  }
  const legacy = await legacyOwner(home, id, project, env);
  if (legacy) conflict(legacy);
}
/** Hold the bot lease through verification and configuration. */
export async function withBotBinding<T>(
  home: string,
  directory: string,
  token: string,
  env: NodeJS.ProcessEnv,
  operation: () => Promise<T>,
): Promise<T> {
  const match = /^(\d+):[A-Za-z0-9_-]+$/.exec(token);
  if (!match?.[1])
    throw new CliError(
      "E_CONFIG",
      "Invalid bot token",
      "Check the bot token and retry",
      3,
    );
  const id = match[1];
  const project = await canonical(directory);
  const path = join(home, ".local", "share", "thc", "bots", `${id}.json`);
  return withSetupLock(home, `bot:${id}`, async () => {
    await assertAvailable(home, path, project, id, env);
    await atomicJson(path, { directory: project });
    try {
      return await operation();
    } catch (error) {
      if (!(await configured(project)))
        await unlink(path).catch((failure: unknown) => {
          if (!isMissing(failure)) throw failure;
        });
      throw error;
    }
  });
}
