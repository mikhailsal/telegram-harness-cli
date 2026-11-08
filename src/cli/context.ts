import { execFile, spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { homedir } from "node:os";
import type { HarnessAdapter } from "../adapters/contract.js";
import type { Config } from "../core/config.js";
import type { Store } from "../store/index.js";
import { promisify } from "node:util";

export interface CommandResult {
  stdout: string;
  stderr: string;
}
export interface CliDependencies {
  probeHistory?: (
    directory: string,
    config: Config,
    store: Store,
  ) => Promise<{ exitCode: number }>;
  openAdapter?: (
    directory: string,
    config: Config,
    store: Store,
  ) => Promise<HarnessAdapter>;
  notifyStartupFailure?: (directory: string, error: unknown) => Promise<void>;
  startBridge?: (directory: string, config: Config) => Promise<void>;
  packageRoot: string;
  cwd: string;
  home: string;
  env: NodeJS.ProcessEnv;
  stdout: (value: string) => void;
  stderr: (value: string) => void;
  readInput: () => Promise<string>;
  exec: (
    file: string,
    args: string[],
    options?: {
      cwd?: string;
      env?: NodeJS.ProcessEnv;
      timeout?: number;
      inherit?: boolean;
    },
  ) => Promise<CommandResult>;
  isTTY: boolean;
  now: () => number;
  sleep: (milliseconds: number) => Promise<void>;
}
const execute = promisify(execFile);
export function defaults(): CliDependencies {
  return {
    packageRoot: fileURLToPath(new URL("../../", import.meta.url)),
    cwd: process.cwd(),
    home: homedir(),
    env: process.env,
    isTTY: process.stdin.isTTY,
    now: () => Date.now(),
    sleep: (milliseconds) =>
      new Promise((resolve) => setTimeout(resolve, milliseconds)),
    stdout: (value) => {
      process.stdout.write(value);
    },
    stderr: (value) => {
      process.stderr.write(value);
    },
    readInput: async () => {
      const chunks: Buffer[] = [];
      for await (const chunk of process.stdin)
        chunks.push(Buffer.from(chunk as Buffer));
      return Buffer.concat(chunks).toString("utf8");
    },
    exec: async (file, args, options) => {
      if (options?.inherit) {
        await new Promise<void>((resolve, reject) => {
          const child = spawn(file, args, {
            cwd: options.cwd,
            env: options.env,
            stdio: "inherit",
          });
          child.once("error", reject);
          child.once("exit", (code) => {
            if (code === 0) resolve();
            else reject(new Error(`Command exited ${String(code)}`));
          });
        });
        return { stdout: "", stderr: "" };
      }
      const result = await execute(file, args, {
        cwd: options?.cwd,
        env: options?.env,
        timeout: options?.timeout,
        maxBuffer: 1024 * 1024,
      });
      return { stdout: result.stdout, stderr: result.stderr };
    },
  };
}
