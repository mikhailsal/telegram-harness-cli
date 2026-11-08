import { createHash, randomUUID } from "node:crypto";
import { mkdir, realpath, rename, unlink, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { createInterface } from "node:readline";
import { Writable } from "node:stream";
import { Bot } from "grammy";
import type { Config } from "../core/config.js";
import type { CliDependencies } from "./context.js";
import { CliError } from "./errors.js";

export async function prompt(
  label: string,
  secret = false,
  input: NodeJS.ReadableStream = process.stdin,
  display: NodeJS.WritableStream = process.stderr,
): Promise<string> {
  const output = secret
    ? new Writable({
        write(_chunk, _encoding, callback) {
          callback();
        },
      })
    : display;
  const rl = createInterface({ input, output, terminal: true });
  try {
    display.write(label);
    return await new Promise<string>((resolve, reject) => {
      let answered = false;
      rl.once("SIGINT", () => {
        rl.close();
      });
      rl.once("close", () => {
        if (!answered)
          reject(
            new CliError(
              "E_NEEDS_INPUT",
              "Input cancelled",
              "thc init --help",
              2,
            ),
          );
      });
      rl.question("", (value) => {
        answered = true;
        resolve(value.trim());
      });
    });
  } finally {
    rl.close();
    if (secret) display.write("\n");
  }
}

export async function requiredInput(
  value: string | undefined,
  name: string,
  deps: CliDependencies,
  label = `${name}: `,
  ask: (label: string) => Promise<string> = prompt,
): Promise<string> {
  if (value) return value;
  if (!deps.isTTY)
    throw new CliError(
      "E_NEEDS_INPUT",
      `Missing required --${name}`,
      `thc init --${name} VALUE --help`,
      2,
    );
  const answer = await ask(label);
  if (!answer)
    throw new CliError(
      "E_NEEDS_INPUT",
      `Missing required --${name}`,
      `thc init --${name} VALUE --help`,
      2,
    );
  return answer;
}

export async function interactiveToken(
  directory: string,
  deps: CliDependencies,
  io: {
    readToken: () => Promise<string>;
    getBot: (token: string) => Promise<{ id: number; username?: string }>;
  } = {
    readToken: () => prompt("Bot token (hidden): ", true),
    getBot: (token) =>
      new Bot(token, { client: { timeoutSeconds: 15 } }).api.getMe(),
  },
): Promise<{ path: string; token: string }> {
  deps.stderr(
    "Create a bot with @BotFather: send /newbot and follow its instructions.\n",
  );
  const token = await io.readToken();
  if (!/^\d+:[A-Za-z0-9_-]+$/.test(token))
    throw new CliError(
      "E_CONFIG",
      "Invalid bot token",
      "Run thc init again",
      3,
    );
  try {
    const bot = await io.getBot(token);
    deps.stderr(
      `Bot verified: @${bot.username ?? "(no username)"} (ID ${String(bot.id)}).\n`,
    );
  } catch {
    throw new CliError(
      "E_CONFIG",
      "Could not verify the bot token with Telegram",
      "Check the token and network, then run thc init again",
      3,
    );
  }
  const canonical = await realpath(directory);
  const hash = createHash("sha256").update(canonical).digest("hex");
  const dir = join(deps.home, ".config", "thc", "tokens");
  return { path: join(dir, `${hash}.token`), token };
}

export async function persistToken(path: string, token: string): Promise<void> {
  const dir = join(path, "..");
  await mkdir(dir, { recursive: true, mode: 0o700 });
  const temporary = join(dir, `.token-${randomUUID()}`);
  try {
    await writeFile(temporary, `${token}\n`, { mode: 0o600, flag: "wx" });
    await rename(temporary, path);
  } finally {
    await unlink(temporary).catch(() => undefined);
  }
}

export async function verifyCodex(
  directory: string,
  config: Config,
  deps: CliDependencies,
  ask: (label: string) => Promise<string> = prompt,
): Promise<void> {
  const options = { cwd: directory, env: deps.env };
  try {
    await deps.exec(config.codex.executable, ["--version"], options);
  } catch {
    throw new CliError(
      "E_DEPENDENCY",
      "Codex is unavailable",
      "Install Codex and run thc init again",
      4,
    );
  }
  try {
    await deps.exec(config.codex.executable, ["login", "status"], options);
    return;
  } catch {
    if (
      !deps.isTTY ||
      !/^(y|yes)$/i.test(
        await ask("Codex is not signed in. Run codex login now? [y/N] "),
      )
    )
      throw new CliError(
        "E_DEPENDENCY",
        "Codex is not signed in",
        "codex login",
        4,
      );
  }
  try {
    await deps.exec(config.codex.executable, ["login"], {
      ...options,
      inherit: true,
    });
    await deps.exec(config.codex.executable, ["login", "status"], options);
  } catch {
    throw new CliError(
      "E_DEPENDENCY",
      "Codex login did not complete",
      "codex login",
      4,
    );
  }
}
