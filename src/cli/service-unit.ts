import { createHash } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { readLockOwner } from "../core/lock.js";
import type { CliDependencies } from "./context.js";
import { CliError } from "./errors.js";

// Path settings such as WorkingDirectory= take the raw rest of the line: systemd
// does not strip quotes there, so a quoted path is rejected as not absolute.
function unitPathValue(value: string): string {
  if (/[\r\n\0]/.test(value))
    throw new CliError(
      "E_USAGE",
      "Invalid path for a systemd unit",
      "thc init --help",
      2,
    );
  return value.replace(/%/g, "%%");
}
// Command lines and Environment= split on whitespace and honour quotes.
function unitQuote(value: string): string {
  return `"${unitPathValue(value).replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"`;
}
function unitName(directory: string): string {
  const id = createHash("sha256").update(directory).digest("hex").slice(0, 24);
  return `thc-${id}.service`;
}
async function isActive(
  directory: string,
  deps: CliDependencies,
): Promise<boolean> {
  try {
    const result = await deps.exec(
      "systemctl",
      ["--user", "is-active", unitName(directory)],
      { env: deps.env },
    );
    return result.stdout.trim() === "active";
  } catch {
    return false;
  }
}
// A unit whose bridge has exited keeps stopping while systemd waits for its
// other processes, and until then systemctl blocks a stop or a start of it.
// The query only decides whether init prints a notice, so it is bounded and a
// stalled or failed systemctl means "not stopping".
export async function isStopping(
  directory: string,
  deps: CliDependencies,
): Promise<boolean> {
  try {
    const result = await deps.exec(
      "systemctl",
      [
        "--user",
        "show",
        unitName(directory),
        "--property",
        "ActiveState",
        "--value",
      ],
      { env: deps.env, timeout: 5000 },
    );
    return result.stdout.trim() === "deactivating";
  } catch {
    return false;
  }
}
// Whether the unit is in `state` with `pid` as its main process. A stalled or
// failed query means "no".
async function unitHolds(
  directory: string,
  deps: CliDependencies,
  pid: number,
  state: "active" | "deactivating",
): Promise<boolean> {
  try {
    const result = await deps.exec(
      "systemctl",
      [
        "--user",
        "show",
        unitName(directory),
        "--property",
        "ActiveState",
        "--property",
        "MainPID",
      ],
      { env: deps.env, timeout: 5000 },
    );
    const lines = result.stdout.split("\n").map((line) => line.trim());
    return (
      lines.includes(`ActiveState=${state}`) &&
      lines.includes(`MainPID=${String(pid)}`)
    );
  } catch {
    return false;
  }
}
/**
 * Stops the bridge `pid` through systemd when it is this folder's running
 * service, so the unit reports "deactivating" until it exits and a later
 * `systemctl start` waits for that. Signalling the process directly would leave
 * the unit "active" meanwhile, and init would take the bridge for a running one.
 * Returns false when systemd does not run it or could not be asked; the caller
 * then signals the process itself.
 */
export async function requestServiceStop(
  directory: string,
  deps: CliDependencies,
  pid: number,
): Promise<boolean> {
  if (!(await unitHolds(directory, deps, pid, "active"))) return false;
  try {
    await deps.exec(
      "systemctl",
      ["--user", "stop", "--no-block", unitName(directory)],
      { env: deps.env, timeout: 5000 },
    );
    return true;
  } catch {
    return false;
  }
}
/**
 * Whether a bridge serves this folder and keeps doing so: an active service,
 * or a live lock owner such as a foreground `thc start`. A lock owner that
 * systemd is already stopping does not count, so init starts the service once
 * that stop finishes.
 */
export async function isRunning(
  directory: string,
  deps: CliDependencies,
): Promise<boolean> {
  if (await isActive(directory, deps)) return true;
  const owner = await readLockOwner(directory);
  // A lock owner that systemd is stopping exits within the stop timeout, and
  // nothing starts it again.
  return (
    owner !== undefined &&
    !(await unitHolds(directory, deps, owner.pid, "deactivating"))
  );
}
// The service must find the managed `thc` shim, which the agent's instructions
// call, even when the installing shell did not have its directory on PATH. It
// goes last so that nothing in that user directory shadows the caller's tools.
function servicePath(
  home: string,
  path: string | undefined,
): string | undefined {
  if (path === undefined) return undefined;
  const managed = join(home, ".local", "bin");
  if (path === "") return managed;
  return path.split(":").includes(managed) ? path : `${path}:${managed}`;
}
export async function configureUnit(
  home: string,
  directory: string,
  version: string,
  path: string | undefined,
): Promise<string> {
  const units = join(home, ".config", "systemd", "user");
  await mkdir(units, { recursive: true });
  const name = unitName(directory);
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
  const unitPath = servicePath(home, path);
  const environment =
    unitPath === undefined
      ? ""
      : `Environment=${unitQuote(`PATH=${unitPath}`)}\n`;
  const text = `[Unit]\nDescription=Telegram harness bridge\nAfter=network-online.target\n\n[Service]\nType=simple\nWorkingDirectory=${unitPathValue(directory)}\n${environment}ExecStart=${unitQuote(process.execPath)} ${unitQuote(executable)} start --dir ${unitQuote(directory)}\nRestart=on-failure\n\n[Install]\nWantedBy=default.target\n`;
  await writeFile(join(units, name), text, { mode: 0o600 });
  return name;
}
