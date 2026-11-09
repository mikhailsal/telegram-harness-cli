import { mkdir, readFile, realpath, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { readLockOwner } from "../core/lock.js";
import { Store } from "../store/index.js";
import { writeAgentInstructions } from "./skill.js";
import { doctor } from "./admin.js";
import { respondWithPrivateIds } from "./identity.js";
import { configSchema, loadConfig, resolveToken } from "../core/config.js";
import type { CliDependencies } from "./context.js";
import type { Arguments } from "./arguments.js";
import { allowedFlags, flag } from "./arguments.js";
import { CliError } from "./errors.js";
import { exists } from "./paths.js";
import { install, registerFolder } from "./install.js";
import {
  interactiveToken,
  persistToken,
  requiredInput,
  verifyCodex,
} from "./onboarding.js";
import { withBotBinding } from "./bot-binding.js";
import { withSetupLock } from "./setup-lock.js";
import { configureUnit, isActive } from "./service-unit.js";
import {
  alreadyRunning,
  assertSettingsMatch,
  configuredDirectory,
  installedTarget,
  markFirstRun,
  onboardingFor,
  reportCliPath,
  type Onboarding,
} from "./init-options.js";

async function ignoreState(directory: string): Promise<void> {
  const path = join(directory, ".gitignore");
  const prior = (await exists(path)) ? await readFile(path, "utf8") : "";
  if (
    prior
      .split(/\r?\n/)
      .some((line) => line.trim() === ".thc/" || line.trim() === ".thc")
  )
    return;
  await writeFile(
    path,
    `${prior}${prior && !prior.endsWith("\n") ? "\n" : ""}.thc/\n`,
  );
}
async function packageVersion(root: string): Promise<string> {
  const data: unknown = JSON.parse(
    await readFile(join(root, "package.json"), "utf8"),
  );
  if (
    typeof data !== "object" ||
    data === null ||
    !("version" in data) ||
    typeof data.version !== "string"
  )
    throw new CliError(
      "E_DEPENDENCY",
      "Package version is unavailable",
      "Reinstall telegram-harness-cli",
      4,
    );
  return data.version;
}
async function tokenValue(
  token: Parameters<typeof resolveToken>[0],
  directory: string,
  deps: CliDependencies,
): Promise<string> {
  try {
    return await resolveToken(token, directory, deps.env);
  } catch {
    throw new CliError(
      "E_CONFIG",
      "Token file or environment variable is missing or invalid",
      "thc init --help",
      3,
    );
  }
}
async function userIdInput(
  value: string | undefined,
  token: string,
  deps: CliDependencies,
): Promise<string> {
  if (value || !deps.isTTY) return requiredInput(value, "user-id", deps);
  const controller = new AbortController();
  const polling = respondWithPrivateIds(token, controller.signal).catch(() => {
    if (!controller.signal.aborted)
      deps.stderr("Unable to poll Telegram for your ID; enter it manually.\n");
  });
  try {
    deps.stderr(
      "Send a private message to your bot; it will reply with your numeric user ID. Enter that ID here to authorize it.\n",
    );
    return await requiredInput(undefined, "user-id", deps);
  } finally {
    controller.abort();
    await polling;
  }
}
async function configForInit(
  args: Arguments,
  deps: CliDependencies,
  token: string,
  secret: string,
) {
  const userId = Number(await userIdInput(flag(args, "user-id"), secret, deps));
  const sandbox = await requiredInput(
    flag(args, "sandbox"),
    "sandbox",
    deps,
    "Sandbox (read-only, workspace-write, or danger-full-access): ",
  );
  const parsed = configSchema.safeParse({
    token: { file: token },
    allowlist: [userId],
    sandbox,
  });
  if (!parsed.success)
    throw new CliError(
      "E_CONFIG",
      parsed.error.issues
        .map((issue) => `${issue.path.join(".")}: ${issue.message}`)
        .join("; "),
      "thc init --help",
      3,
    );
  return parsed.data;
}
async function waitForFirstAnswer(
  directory: string,
  config: Awaited<ReturnType<typeof loadConfig>>,
  start: string,
  deps: CliDependencies,
): Promise<void> {
  const startMs = Date.parse(start);
  const deadline = startMs + 120000;
  deps.stdout("Bridge started. Send a message to the bot.\n");
  while (deps.now() < deadline) {
    const store = new Store(join(directory, ".thc", "store.db"), true);
    let delivered: string | undefined;
    try {
      delivered = store.getCursor("first-delivery-at");
    } finally {
      store.close();
    }
    if (delivered && Date.parse(delivered) >= startMs) {
      deps.stdout(
        `Working: first answer in ${String(Math.round((Date.parse(delivered) - startMs) / 1000))} s.\n`,
      );
      return;
    }
    await deps.sleep(500);
  }
  const checks = await doctor(directory, config, deps);
  deps.stdout(
    `No first answer within 120 s. Doctor: ${JSON.stringify(checks)}\n`,
  );
}
async function checkLinger(
  directory: string,
  deps: CliDependencies,
): Promise<void> {
  try {
    const result = await deps.exec(
      "loginctl",
      [
        "show-user",
        String(process.getuid?.() ?? ""),
        "-p",
        "Linger",
        "--value",
      ],
      { cwd: directory, env: deps.env, timeout: 5000 },
    );
    if (result.stdout.trim() === "yes") return;
  } catch {
    /* Report the same remedy when loginctl is unavailable. */
  }
  deps.stderr(
    'User linger is not enabled or could not be checked; run: loginctl enable-linger "$USER"\n',
  );
}
async function enableService(
  unit: string,
  directory: string,
  config: Awaited<ReturnType<typeof loadConfig>>,
  deps: CliDependencies,
  restart: boolean,
  onboarding: Onboarding,
): Promise<void> {
  if (
    !restart &&
    ((await isActive(directory, deps)) || (await readLockOwner(directory)))
  )
    return;
  if (restart)
    await deps.exec("systemctl", ["--user", "stop", unit], { env: deps.env });
  const start = new Date(deps.now()).toISOString();
  const firstRun = markFirstRun(directory, onboarding, start);
  await deps.exec("systemctl", ["--user", "daemon-reload"], { env: deps.env });
  await deps.exec("systemctl", ["--user", "enable", "--now", unit], {
    env: deps.env,
  });
  if (firstRun) await waitForFirstAnswer(directory, config, start, deps);
}
async function writeInstalledState(
  directory: string,
  version: string,
  config: Awaited<ReturnType<typeof loadConfig>>,
  args: Arguments,
  deps: CliDependencies,
  existing: boolean,
): Promise<void> {
  const configPath = join(directory, ".thc", "config.json");
  await mkdir(join(directory, ".thc"), { recursive: true, mode: 0o700 });
  if (!(await exists(configPath)))
    await writeFile(configPath, `${JSON.stringify(config, null, 2)}\n`, {
      mode: 0o600,
      flag: "wx",
    });
  await ignoreState(directory);
  await writeAgentInstructions(
    directory,
    deps.packageRoot,
    args.flags.has("force"),
  );
  await registerFolder(deps.home, directory, version);
  const unit = await configureUnit(
    deps.home,
    directory,
    version,
    deps.env.PATH,
  );
  if (!args.flags.has("no-start"))
    await enableService(
      unit,
      directory,
      config,
      deps,
      args.flags.has("update") || Boolean(flag(args, "to")),
      await onboardingFor(deps.home, version, existing),
    );
}
export async function init(
  args: Arguments,
  deps: CliDependencies,
): Promise<void> {
  allowedFlags(args, [
    "token-file",
    "user-id",
    "sandbox",
    "yes",
    "no-start",
    "update",
    "to",
    "force",
  ]);
  if (args.positionals.length)
    throw new CliError(
      "E_USAGE",
      "init does not accept positional arguments",
      "thc init --help",
      2,
    );
  const requested = resolve(flag(args, "dir") ?? deps.cwd);
  // A project folder that does not exist yet is created, as it always was.
  await mkdir(requested, { recursive: true });
  const directory = await realpath(requested);
  await withSetupLock(deps.home, `project:${directory}`, async () =>
    initialize(args, deps, await configuredDirectory(directory)),
  );
}
async function initialize(
  args: Arguments,
  deps: CliDependencies,
  directory: string,
): Promise<void> {
  const configPath = join(directory, ".thc", "config.json");
  const existing = await exists(configPath);
  if (!existing && (args.flags.has("update") || flag(args, "to"))) {
    await loadConfig(directory);
  }
  const prior = existing ? await loadConfig(directory) : undefined;
  if (prior) assertSettingsMatch(args, prior, directory);
  const credentials = await initCredentials(args, deps, directory, prior);
  await withBotBinding(
    deps.home,
    directory,
    credentials.secret,
    deps.env,
    async () => {
      if (prior && (await alreadyRunning(args, directory, deps, isActive))) {
        deps.stdout(
          `Already initialized and running: ${directory}. Configuration edits take effect after thc stop and thc init.\n`,
        );
        return;
      }
      await completeInit(args, deps, directory, prior, credentials);
    },
  );
}
interface InitCredentials {
  secret: string;
  tokenPath?: string;
  interactive?: { path: string; token: string };
}
async function initCredentials(
  args: Arguments,
  deps: CliDependencies,
  directory: string,
  prior: Awaited<ReturnType<typeof loadConfig>> | undefined,
): Promise<InitCredentials> {
  if (prior) return { secret: await tokenValue(prior.token, directory, deps) };
  const provided = flag(args, "token-file");
  if (!provided && deps.isTTY) {
    const interactive = await interactiveToken(directory, deps);
    return {
      secret: interactive.token,
      tokenPath: interactive.path,
      interactive,
    };
  }
  const tokenPath = resolve(await requiredInput(provided, "token-file", deps));
  return {
    tokenPath,
    secret: await tokenValue({ file: tokenPath }, directory, deps),
  };
}
async function completeInit(
  args: Arguments,
  deps: CliDependencies,
  directory: string,
  prior: Awaited<ReturnType<typeof loadConfig>> | undefined,
  credentials: InitCredentials,
): Promise<void> {
  const { tokenPath, secret, interactive } = credentials;
  const existing = prior !== undefined;
  const config =
    prior ?? (await configForInit(args, deps, tokenPath ?? "", secret));
  await verifyCodex(directory, config, deps);
  await checkLinger(directory, deps);
  const version = await install(
    {
      directory,
      home: deps.home,
      packageRoot: deps.packageRoot,
      exec: deps.exec,
    },
    await packageVersion(deps.packageRoot),
    await installedTarget(args, directory, existing),
  );
  if (interactive) await persistToken(interactive.path, interactive.token);
  await writeInstalledState(directory, version, config, args, deps, existing);
  deps.stdout(`Initialized ${directory} with thc ${version}.\n`);
  reportCliPath(deps);
}
