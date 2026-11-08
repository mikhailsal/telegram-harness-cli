import { spawnSync } from "node:child_process";
import { chmodSync, copyFileSync, rmSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";
import { resolve } from "node:path";

export function build(
  root = fileURLToPath(new URL("../", import.meta.url)),
): void {
  rmSync(resolve(root, "dist"), { recursive: true, force: true });
  const result = spawnSync(
    process.execPath,
    ["node_modules/typescript/bin/tsc", "-p", "tsconfig.build.json"],
    { stdio: "inherit", cwd: root },
  );
  if (result.error) throw result.error;
  if (result.status !== 0)
    throw new Error(`TypeScript build failed (${String(result.status ?? 1)})`);
  chmodSync(resolve(root, "dist/cli/main.js"), 0o755);
  copyFileSync(
    resolve(root, "package-lock.json"),
    resolve(root, "npm-shrinkwrap.json"),
  );
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href)
  build();
