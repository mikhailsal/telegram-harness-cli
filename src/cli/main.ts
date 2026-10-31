#!/usr/bin/env node
import { realpathSync } from "node:fs";
import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { randomUUID } from "node:crypto";
import { ConfigurationError, loadConfig, type Config } from "../core/config.js";
import { AlreadyRunningError } from "../core/lock.js";
import {
  parseArguments,
  flag,
  allowedFlags,
  type Arguments,
} from "./arguments.js";
import { defaults, type CliDependencies } from "./context.js";
import { CliError, explanations, usage } from "./errors.js";
import { redactSecrets } from "./secrets.js";
import { help, isCommand } from "./help.js";
import { findDirectory } from "./paths.js";
import { init } from "./init.js";
import { doctor, render, renderMarkdown, status, stop, tail } from "./admin.js";
import { historyCommand } from "./history.js";
import { attach } from "./attach.js";

export type { CliDependencies } from "./context.js";
function writeResult(
  deps: CliDependencies,
  json: boolean,
  value: unknown,
): void {
  deps.stdout(
    json ? `${JSON.stringify(value)}\n` : `${JSON.stringify(value, null, 2)}\n`,
  );
}
async function runtimeStart(
  deps: CliDependencies,
): Promise<(directory: string, config: Config) => Promise<void>> {
  if (deps.startBridge) return deps.startBridge;
  const path = "../core/runtime.js";
  const loaded: unknown = await import(path);
  if (
    typeof loaded !== "object" ||
    loaded === null ||
    !("startBridge" in loaded) ||
    typeof loaded.startBridge !== "function"
  )
    throw new CliError(
      "E_DEPENDENCY",
      "Bridge runtime is unavailable",
      "Reinstall telegram-harness-cli",
      4,
    );
  return loaded.startBridge as (
    directory: string,
    config: Config,
  ) => Promise<void>;
}
function startupFailure(error: unknown): boolean {
  if (error instanceof AlreadyRunningError) return false;
  if (error instanceof ConfigurationError || error instanceof SyntaxError)
    return true;
  if (!(error instanceof Error)) return false;
  const path =
    "path" in error && typeof error.path === "string" ? error.path : "";
  return (
    /codex|login|auth|sqlite|database|store|schema/i.test(error.message) ||
    path.endsWith("/.thc/config.json")
  );
}
async function notifyFailure(
  directory: string,
  error: unknown,
  deps: CliDependencies,
): Promise<void> {
  if (!startupFailure(error)) return;
  try {
    if (deps.notifyStartupFailure) {
      await deps.notifyStartupFailure(directory, error);
      return;
    }
    const path = "../core/runtime.js";
    const loaded: unknown = await import(path);
    if (
      typeof loaded !== "object" ||
      loaded === null ||
      !("notifyStartupFailure" in loaded) ||
      typeof loaded.notifyStartupFailure !== "function"
    )
      return;
    const notify = loaded.notifyStartupFailure as (
      directory: string,
      error: unknown,
    ) => Promise<void>;
    await notify(directory, error);
  } catch {
    /* Preserve the original startup error if notification fails. */
  }
}
async function startCommand(
  args: Arguments,
  deps: CliDependencies,
  directory: string,
): Promise<void> {
  allowedFlags(args, []);
  try {
    const config = await loadConfig(directory);
    const start = await runtimeStart(deps);
    await start(directory, config);
  } catch (error) {
    await notifyFailure(directory, error, deps);
    throw error;
  }
}
async function configured(
  args: Arguments,
  deps: CliDependencies,
  directory: string,
): Promise<void> {
  const config = await loadConfig(directory);
  if (args.command === "doctor") {
    allowedFlags(args, []);
    writeResult(
      deps,
      args.flags.has("json"),
      await doctor(directory, config, deps),
    );
    return;
  }
  if (["search", "around", "day"].includes(args.command)) {
    await historyCommand(args, deps, directory, config);
    return;
  }
  if (args.command === "attach") {
    await attach(args, deps, directory, config);
    return;
  }
  throw usage(`Unknown command ${args.command}`);
}
function explain(args: Arguments, deps: CliDependencies): void {
  allowedFlags(args, []);
  const code = args.positionals[0];
  if (!code || !Object.hasOwn(explanations, code))
    throw usage("Unknown error code");
  deps.stdout(`${code}: ${explanations[code] ?? ""}\n`);
}
function showHelp(args: Arguments, deps: CliDependencies): boolean {
  if (args.command !== "help" && !args.flags.has("help")) return false;
  deps.stdout(`${help(args.command)}\n`);
  return true;
}
async function basicCommand(
  args: Arguments,
  deps: CliDependencies,
  directory: string,
): Promise<void> {
  if (args.command === "stop") {
    allowedFlags(args, []);
    await stop(directory, deps);
    return;
  }
  if (args.command === "status") {
    allowedFlags(args, []);
    writeResult(deps, args.flags.has("json"), await status(directory));
    return;
  }
  if (args.command === "tail") {
    await tail(args, directory, deps);
    return;
  }
  render(args, directory, deps);
}
async function dispatch(args: Arguments, deps: CliDependencies): Promise<void> {
  if (showHelp(args, deps)) return;
  if (!isCommand(args.command)) throw usage(`Unknown command ${args.command}`);
  if (args.command === "explain") {
    explain(args, deps);
    return;
  }
  if (args.command === "init") {
    await init(args, deps);
    return;
  }
  if (args.command === "render" && !flag(args, "turn")) {
    await renderMarkdown(args, deps);
    return;
  }
  const directory = await findDirectory(
    deps.cwd,
    flag(args, "dir") ?? deps.env.THC_DIR,
  );
  if (["stop", "status", "tail", "render"].includes(args.command)) {
    await basicCommand(args, deps, directory);
    return;
  }
  if (args.command === "start") {
    await startCommand(args, deps, directory);
    return;
  }
  await configured(args, deps, directory);
}
function hasSystemCode(error: unknown): boolean {
  return (
    error instanceof Error && "code" in error && typeof error.code === "string"
  );
}
function expectedError(
  error: unknown,
  deps: CliDependencies,
): CliError | undefined {
  if (error instanceof CliError) return error;
  if (error instanceof AlreadyRunningError)
    return new CliError("E_ALREADY_RUNNING", error.message, "thc status", 3);
  if (error instanceof ConfigurationError || error instanceof SyntaxError)
    return new CliError(
      "E_CONFIG",
      redactSecrets(error.message, deps.env),
      "thc doctor",
      3,
    );
  const message = error instanceof Error ? error.message : String(error);
  if (/database is locked|SQLITE_BUSY/i.test(message))
    return new CliError("E_STORE_BUSY", "Store is busy", "thc doctor", 4);
  if (/Unsupported database version/i.test(message))
    return new CliError(
      "E_STORE_SCHEMA_NEWER",
      "Store schema is incompatible",
      "thc init --update",
      4,
    );
  if (/requires migration/i.test(message))
    return new CliError(
      "E_DEPENDENCY",
      "Store requires migration",
      "thc start",
      4,
    );
  if (hasSystemCode(error))
    return new CliError(
      "E_DEPENDENCY",
      redactSecrets(message, deps.env),
      "thc doctor",
      4,
    );
}
async function errorFor(
  error: unknown,
  deps: CliDependencies,
): Promise<CliError> {
  const expected = expectedError(error, deps);
  if (expected) return expected;
  const message = error instanceof Error ? error.message : String(error);
  const path = join(deps.cwd, ".thc", "log", `crash-${randomUUID()}.txt`);
  try {
    await mkdir(join(deps.cwd, ".thc", "log"), {
      recursive: true,
      mode: 0o700,
    });
    await writeFile(
      path,
      redactSecrets(
        error instanceof Error ? (error.stack ?? message) : message,
        deps.env,
      ),
      { mode: 0o600, flag: "wx" },
    );
  } catch {
    return new CliError(
      "E_INTERNAL",
      "Unexpected error; unable to write a trace",
      "thc doctor",
      5,
    );
  }
  return new CliError(
    "E_INTERNAL",
    `Unexpected error; trace: ${path}`,
    "thc doctor",
    5,
  );
}
export async function run(
  argv: string[],
  overrides: Partial<CliDependencies> = {},
): Promise<number> {
  const deps = { ...defaults(), ...overrides };
  try {
    await dispatch(parseArguments(argv), deps);
    return 0;
  } catch (error) {
    const failure = await errorFor(error, deps);
    if (argv.includes("--json"))
      deps.stdout(`${JSON.stringify({ error: failure.info })}\n`);
    else
      deps.stderr(
        `thc: ${failure.info.code}: ${failure.info.what}\nfix: ${failure.info.fix}\n`,
      );
    return failure.exitCode;
  }
}
function invokedDirectly(): boolean {
  try {
    return Boolean(
      process.argv[1] &&
      realpathSync(process.argv[1]) === fileURLToPath(import.meta.url),
    );
  } catch {
    return false;
  }
}
if (invokedDirectly()) process.exitCode = await run(process.argv.slice(2));
