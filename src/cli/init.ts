import { mkdir, readFile, writeFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import { join, resolve } from "node:path";
import { createInterface } from "node:readline/promises";
import { openStore, Store } from "../store/index.js";
import { writeAgentInstructions } from "./skill.js";
import { respondWithPrivateIds } from "./identity.js";
import { doctor } from "./admin.js";
import { configSchema, loadConfig, resolveToken } from "../core/config.js";
import type { CliDependencies } from "./context.js";
import type { Arguments } from "./arguments.js";
import { allowedFlags, flag } from "./arguments.js";
import { CliError } from "./errors.js";
import { exists } from "./paths.js";
import { install, registerFolder } from "./install.js";

async function input(
  value: string | undefined,
  name: string,
  deps: CliDependencies,
): Promise<string> {
  if (value) return value;
  if (!deps.isTTY)
    throw new CliError(
      "E_NEEDS_INPUT",
      `Missing required --${name}`,
      `thc init --${name} VALUE --help`,
      2,
    );
  const rl = createInterface({ input: process.stdin, output: process.stderr });
  try {
    const answer = (await rl.question(`${name}: `)).trim();
    if (!answer)
      throw new CliError(
        "E_NEEDS_INPUT",
        `Missing required --${name}`,
        `thc init --${name} VALUE --help`,
        2,
      );
    return answer;
  } finally {
    rl.close();
  }
}
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
function unitQuote(value: string): string {
  if (/[\r\n\0]/.test(value))
    throw new CliError(
      "E_USAGE",
      "Invalid path for a systemd unit",
      "thc init --help",
      2,
    );
  return `"${value.replace(/%/g, "%%").replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"`;
}
async function configureUnit(
  home: string,
  directory: string,
  version: string,
  path: string | undefined,
): Promise<string> {
  const units = join(home, ".config", "systemd", "user");
  await mkdir(units, { recursive: true });
  const id = createHash("sha256").update(directory).digest("hex").slice(0, 24);
  const name = `thc-${id}.service`;
  const executable = join(
    home,
    ".local",
    "share",
    "thc",
    version,
    "dist",
    "cli",
    "main.js",
  );
  const environment =
    path === undefined ? "" : `Environment=${unitQuote(`PATH=${path}`)}\n`;
  const text = `[Unit]\nDescription=Telegram harness bridge\nAfter=network-online.target\n\n[Service]\nType=simple\nWorkingDirectory=${unitQuote(directory)}\n${environment}ExecStart=${unitQuote(process.execPath)} ${unitQuote(executable)} start --dir ${unitQuote(directory)}\nRestart=on-failure\n\n[Install]\nWantedBy=default.target\n`;
  await writeFile(join(units, name), text, { mode: 0o600 });
  return name;
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
  if (value || !deps.isTTY) return input(value, "user-id", deps);
  const controller = new AbortController();
  const polling = respondWithPrivateIds(token, controller.signal).catch(() => {
    if (!controller.signal.aborted)
      deps.stderr("Unable to poll Telegram for your ID; enter it manually.\n");
  });
  try {
    deps.stderr("Send a private message to the bot to receive your user ID.\n");
    return await input(undefined, "user-id", deps);
  } finally {
    controller.abort();
    await polling;
  }
}
async function configForInit(
  args: Arguments,
  deps: CliDependencies,
  directory: string,
) {
  if (args.flags.has("update") || flag(args, "to"))
    return loadConfig(directory);
  const token = resolve(
    await input(flag(args, "token-file"), "token-file", deps),
  );
  const secret = await tokenValue({ file: token }, directory, deps);
  const userId = Number(await userIdInput(flag(args, "user-id"), secret, deps));
  const sandbox = await input(flag(args, "sandbox"), "sandbox", deps);
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
  await tokenValue(parsed.data.token, directory, deps);
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
async function verifyCodex(
  directory: string,
  config: Awaited<ReturnType<typeof loadConfig>>,
  deps: CliDependencies,
): Promise<void> {
  try {
    await deps.exec(config.codex.executable, ["--version"], {
      cwd: directory,
      env: deps.env,
    });
    await deps.exec(config.codex.executable, ["login", "status"], {
      cwd: directory,
      env: deps.env,
    });
  } catch {
    throw new CliError(
      "E_DEPENDENCY",
      "Codex is unavailable or not signed in",
      "codex login",
      4,
    );
  }
}
async function enableService(
  unit: string,
  directory: string,
  config: Awaited<ReturnType<typeof loadConfig>>,
  deps: CliDependencies,
  restart: boolean,
): Promise<void> {
  if (restart)
    await deps.exec("systemctl", ["--user", "stop", unit], { env: deps.env });
  const start = new Date(deps.now()).toISOString();
  const store = openStore(join(directory, ".thc", "store.db"));
  try {
    store.setCursor("first-delivery-at", "");
    store.setCursor("welcome-pending", start);
  } finally {
    store.close();
  }
  await deps.exec("systemctl", ["--user", "daemon-reload"], { env: deps.env });
  await deps.exec("systemctl", ["--user", "enable", "--now", unit], {
    env: deps.env,
  });
  await waitForFirstAnswer(directory, config, start, deps);
}
async function writeInstalledState(
  directory: string,
  version: string,
  config: Awaited<ReturnType<typeof loadConfig>>,
  args: Arguments,
  deps: CliDependencies,
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
  const directory = resolve(flag(args, "dir") ?? deps.cwd);
  const config = await configForInit(args, deps, directory);
  await tokenValue(config.token, directory, deps);
  const configPath = join(directory, ".thc", "config.json");
  if (
    !args.flags.has("update") &&
    !flag(args, "to") &&
    (await exists(configPath))
  )
    throw new CliError(
      "E_CONFIG",
      "This folder is already initialized",
      "thc init --update --no-start",
      3,
    );
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
    flag(args, "to"),
  );
  await writeInstalledState(directory, version, config, args, deps);
  deps.stdout(`Initialized ${directory} with thc ${version}.\n`);
}
