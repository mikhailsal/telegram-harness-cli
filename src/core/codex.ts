import { execFile as execFileCallback } from "node:child_process";
import { open, readdir, realpath } from "node:fs/promises";
import { createRequire } from "node:module";
import { dirname, isAbsolute, join, resolve } from "node:path";
import { promisify } from "node:util";

export const execFile = promisify(execFileCallback);

async function isNative(path: string): Promise<boolean> {
  try {
    const file = await open(path, "r");
    try {
      const bytes = Buffer.alloc(4);
      await file.read(bytes, 0, 4, 0);
      return bytes.equals(Buffer.from([0x7f, 0x45, 0x4c, 0x46]));
    } finally {
      await file.close();
    }
  } catch {
    return false;
  }
}

async function vendorBinary(vendor: string): Promise<string | undefined> {
  const architecture = process.arch === "arm64" ? "aarch64" : "x86_64";
  let entries: string[];
  try {
    entries = await readdir(vendor);
  } catch {
    return;
  }
  for (const entry of entries) {
    if (!entry.startsWith(architecture) || !entry.includes("linux")) continue;
    for (const directory of ["codex", "bin"]) {
      const candidate = join(vendor, entry, directory, "codex");
      if (await isNative(candidate)) return realpath(candidate);
    }
  }
  return;
}

export async function nativeCodex(
  executable: string,
  directory: string,
): Promise<string> {
  const located =
    isAbsolute(executable) || executable.includes("/")
      ? resolve(directory, executable)
      : (await execFile("which", ["--", executable])).stdout.trim();
  const path = await realpath(located);
  if (await isNative(path)) return path;
  const candidates = [join(dirname(path), "..", "vendor")];
  const packageName =
    process.arch === "arm64" ? "codex-linux-arm64" : "codex-linux-x64";
  try {
    const manifest = createRequire(path).resolve(
      `@openai/${packageName}/package.json`,
    );
    candidates.push(join(dirname(manifest), "vendor"));
  } catch {
    candidates.push(
      join(
        dirname(path),
        "..",
        "node_modules",
        "@openai",
        packageName,
        "vendor",
      ),
    );
  }
  for (const candidate of candidates) {
    const binary = await vendorBinary(candidate);
    if (binary) return binary;
  }
  throw new Error(
    "Cannot locate native Codex executable; set codex.executable to its absolute path",
  );
}

export function sandboxPolicy(
  sandbox: string,
  directory: string,
): Record<string, unknown> {
  if (sandbox === "danger-full-access") return { type: "dangerFullAccess" };
  if (sandbox === "read-only")
    return { type: "readOnly", networkAccess: false };
  if (sandbox === "workspace-write")
    return {
      type: "workspaceWrite",
      writableRoots: [directory],
      networkAccess: false,
    };
  throw new Error("Unsupported sandbox mode");
}
