import { execFile } from "node:child_process";
import { promisify } from "node:util";

const execute = promisify(execFile);

export type DaemonExec = (
  file: string,
  args: string[],
  options: {
    cwd: string;
    timeout: number;
    maxBuffer: number;
    signal: AbortSignal;
  },
) => Promise<unknown>;

export interface DaemonStartOptions {
  executable: string;
  directory: string;
  env: NodeJS.ProcessEnv;
  signal: AbortSignal;
  log: (event: string, details?: Record<string, unknown>) => void;
  exec?: DaemonExec;
}

const START = ["app-server", "daemon", "start"];

// systemd keeps every process a service forks in that service's cgroup and
// signals them all when the service stops. The Codex daemon is shared by every
// client of CODEX_HOME and must outlive the bridge, so under systemd it starts
// in its own transient scope. A direct start would put it back in the bridge
// unit, so there is no fallback: the supervisor retries instead.
export function daemonCommand(
  executable: string,
  env: NodeJS.ProcessEnv,
): { file: string; args: string[]; isolated: boolean } {
  if (!env.INVOCATION_ID)
    return { file: executable, args: START, isolated: false };
  return {
    file: "systemd-run",
    args: [
      "--user",
      "--scope",
      "--collect",
      "--quiet",
      "--description=Codex app-server daemon (started by thc)",
      "--",
      executable,
      ...START,
    ],
    isolated: true,
  };
}

function failureCode(error: unknown): number | string {
  if (typeof error !== "object" || error === null) return "unknown";
  const code = (error as { code?: unknown }).code;
  return typeof code === "number" || typeof code === "string"
    ? code
    : "unknown";
}

export async function startDaemon(options: DaemonStartOptions): Promise<void> {
  const command = daemonCommand(options.executable, options.env);
  const exec = options.exec ?? execute;
  try {
    await exec(command.file, command.args, {
      cwd: options.directory,
      timeout: 30000,
      maxBuffer: 1024 * 1024,
      signal: options.signal,
    });
  } catch (error) {
    if (!options.signal.aborted)
      options.log("daemon.start-failed", {
        isolated: command.isolated,
        code: failureCode(error),
      });
    throw error;
  }
}
