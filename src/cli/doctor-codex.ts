import { join } from "node:path";
import { SettingsUnsupportedError } from "../adapters/contract.js";
import { listModels } from "../adapters/codex-protocol/settings.js";
import { ProtocolTransport } from "../adapters/codex-protocol/transport.js";
import { codexHome, type Config } from "../core/config.js";
import type { CliDependencies } from "./context.js";

// The Codex version doctor's checks were tested against.
export const fixture = "0.169.0";
const modelListBudgetMs = 5000;

// One model/list call on its own connection to the Codex daemon. Doctor never
// starts the daemon; aborting drops the connection, which settles every call
// even when the daemon hangs.
export async function askModelList(
  socket: string,
  signal: AbortSignal,
): Promise<void> {
  const transport = new ProtocolTransport(
    () => undefined,
    () => undefined,
  );
  const abort = () => {
    transport.terminate();
  };
  signal.addEventListener("abort", abort, { once: true });
  try {
    await transport.connect(socket);
    await listModels(transport, signal);
  } finally {
    signal.removeEventListener("abort", abort);
    transport.close();
  }
}

function missingDaemon(error: unknown): boolean {
  const code = (error as NodeJS.ErrnoException | undefined)?.code;
  return code === "ENOENT" || code === "ECONNREFUSED";
}

// Whether /model and /effort can work: the protocol adapter asks the Codex
// daemon for model/list, which codex-cli answers from 0.169.0 on.
export async function modelListCheck(
  config: Config,
  deps: CliDependencies,
): Promise<string> {
  if (config.codex.adapter !== "protocol")
    return "not checked: the tmux adapter does not switch models";
  const socket =
    config.codex.socket ??
    join(codexHome(config), "app-server-control", "app-server-control.sock");
  const signal = AbortSignal.timeout(modelListBudgetMs);
  try {
    await (deps.listCodexModels ?? askModelList)(socket, signal);
    return "supported";
  } catch (error) {
    if (error instanceof SettingsUnsupportedError)
      return `unsupported: /model needs codex-cli ${fixture} or newer`;
    if (missingDaemon(error))
      return "unavailable: the Codex daemon is not running";
    return signal.aborted ? "failed: timeout" : "failed";
  }
}

export async function versionCheck(
  config: Config,
  directory: string,
  deps: CliDependencies,
): Promise<{
  version: string | null;
  fixture: string;
  matches: boolean | null;
  modelList: string;
}> {
  const modelList = await modelListCheck(config, deps);
  try {
    const result = await deps.exec(config.codex.executable, ["--version"], {
      cwd: directory,
      env: deps.env,
      timeout: 5000,
    });
    const version = /\b(\d+\.\d+\.\d+)\b/.exec(result.stdout)?.[1] ?? null;
    return {
      version,
      fixture,
      matches: version === null ? null : version === fixture,
      modelList,
    };
  } catch {
    return { version: null, fixture, matches: null, modelList };
  }
}
