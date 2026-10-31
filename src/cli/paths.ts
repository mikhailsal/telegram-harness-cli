import { access } from "node:fs/promises";
import { basename, dirname, join, resolve } from "node:path";
import { CliError } from "./errors.js";

export async function exists(path: string): Promise<boolean> {
  try {
    await access(path);
    return true;
  } catch (error) {
    if (error instanceof Error && "code" in error && error.code === "ENOENT")
      return false;
    throw error;
  }
}
export async function findDirectory(
  cwd: string,
  override?: string,
): Promise<string> {
  if (override) {
    const value = resolve(override);
    if ((await exists(value)) && basename(value) === ".thc")
      return dirname(value);
    if (await exists(join(value, ".thc"))) return value;
    throw new CliError(
      "E_NO_STATE_DIR",
      "No .thc directory at the requested path",
      "thc init --help",
      3,
    );
  }
  let current = resolve(cwd);
  while (current !== dirname(current)) {
    if (await exists(join(current, ".thc"))) return current;
    current = dirname(current);
  }
  if (await exists(join(current, ".thc"))) return current;
  throw new CliError(
    "E_NO_STATE_DIR",
    "No .thc directory found in this folder or its parents",
    "thc init --help",
    3,
  );
}
