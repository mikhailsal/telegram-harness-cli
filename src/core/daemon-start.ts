import { execFile } from "node:child_process";
import { readFileSync } from "node:fs";
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
  cgroup?: () => string;
}

const START = ["app-server", "daemon", "start"];

// systemd keeps every process a service forks in that service's cgroup and
// signals them all when the service stops. The Codex daemon is shared by every
// client of CODEX_HOME and must outlive the bridge, so when the bridge runs as a
// systemd user service the daemon starts in its own transient user scope. A
// direct start would put it back in the bridge unit, so there is no fallback
// there: the supervisor retries instead. Outside the user manager (a system
// unit, or a shell that merely inherited INVOCATION_ID) there is no user scope
// to move to, so Codex starts directly as before.
const USER_SERVICE = /\/user@\d+\.service\//;
export function daemonCommand(
  executable: string,
  env: NodeJS.ProcessEnv,
  cgroup: string,
): { file: string; args: string[]; isolated: boolean } {
  if (!env.INVOCATION_ID || !USER_SERVICE.test(cgroup))
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
// Read only when INVOCATION_ID says systemd started us, so /proc exists.
export function ownCgroup(): string {
  return readFileSync("/proc/self/cgroup", "utf8");
}

function failureCode(error: unknown): number | string {
  if (typeof error !== "object" || error === null) return "unknown";
  const code = (error as { code?: unknown }).code;
  return typeof code === "number" || typeof code === "string"
    ? code
    : "unknown";
}

export async function startDaemon(options: DaemonStartOptions): Promise<void> {
  const cgroup = options.env.INVOCATION_ID
    ? (options.cgroup ?? ownCgroup)()
    : "";
  const command = daemonCommand(options.executable, options.env, cgroup);
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
