import { spawnSync } from "node:child_process";

export default function buildArtifact(): void {
  const result = spawnSync(process.execPath, ["scripts/build.ts"], {
    cwd: process.cwd(),
    encoding: "utf8",
  });
  if (result.error) throw result.error;
  if (result.status !== 0)
    throw new Error(`Application build failed: ${result.stderr}`);
}
