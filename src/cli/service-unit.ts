import { createHash } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";
import type { CliDependencies } from "./context.js";
import { CliError } from "./errors.js";

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
function unitName(directory: string): string {
  const id = createHash("sha256").update(directory).digest("hex").slice(0, 24);
  return `thc-${id}.service`;
}
export async function isActive(
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
  const text = `[Unit]\nDescription=Telegram harness bridge\nAfter=network-online.target\n\n[Service]\nType=simple\nWorkingDirectory=${unitQuote(directory)}\n${environment}ExecStart=${unitQuote(process.execPath)} ${unitQuote(executable)} start --dir ${unitQuote(directory)}\nRestart=on-failure\n\n[Install]\nWantedBy=default.target\n`;
  await writeFile(join(units, name), text, { mode: 0o600 });
  return name;
}
