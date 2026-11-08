import { spawnSync } from "node:child_process";
import { cp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import {
  artifactFingerprint,
  prepareBuild,
  worktreeFingerprint,
} from "./release.ts";
import type { BuildResult, LiveRecord } from "./release.ts";

const recordPath = ".quality/test-live.json";
function command(program: string, args: string[]): string {
  const result = spawnSync(program, args, { encoding: "utf8", timeout: 30000 });
  if (result.error || result.status !== 0)
    throw new Error(`${program} ${args.join(" ")} failed`);
  return result.stdout.trim();
}
export function newRecord(): LiveRecord {
  return {
    schema: 2,
    date: new Date().toISOString(),
    head: "",
    tree: "",
    version: "",
    codex: "",
    adapters: {},
    result: "failed",
    dirty: true,
    fingerprint: "",
    artifact: "",
    node: "",
    npm: "",
  };
}
type Acceptance = (adapter: "protocol" | "tmux", root: string) => Promise<void>;
export async function checkedScenario(
  root: string,
): Promise<{ acceptance: Acceptance }> {
  const artifact = artifactFingerprint(root);
  await cp(
    new URL("../tests/live/", import.meta.url),
    join(root, "tests/live"),
    {
      recursive: true,
    },
  );
  await mkdir(join(root, "tests/fakes"), { recursive: true });
  await cp(
    new URL("../tests/fakes/servers.ts", import.meta.url),
    join(root, "tests/fakes/servers.ts"),
  );
  const config = join(root, "vitest.live.config.mjs");
  await writeFile(
    config,
    `export default ${JSON.stringify({ cacheDir: join(root, ".live-cache"), test: { cache: false, fsModuleCache: false, include: ["tests/live/*.test.ts"] } })};\n`,
  );
  command(process.execPath, [
    join(root, "node_modules/vitest/vitest.mjs"),
    "run",
    "--root",
    root,
    "--config",
    config,
    "--configLoader",
    "native",
  ]);
  verifyArtifact(root, artifact);
  const loaded: unknown = await import(
    pathToFileURL(join(root, "tests/live/live-scenario.ts")).href
  );
  if (
    !loaded ||
    typeof loaded !== "object" ||
    !("acceptance" in loaded) ||
    typeof loaded.acceptance !== "function"
  )
    throw new Error("Live scenario is unavailable");
  verifyArtifact(root, artifact);
  return loaded as { acceptance: Acceptance };
}
function verifyArtifact(root: string, expected: string): void {
  if (artifactFingerprint(root) !== expected)
    throw new Error("Build artifact changed during live self-check");
}
export interface AcceptanceOperations {
  command: typeof command;
  fingerprint: typeof worktreeFingerprint;
  artifact: typeof artifactFingerprint;
  build: typeof prepareBuild;
  scenario: typeof checkedScenario;
  packageFile: () => Promise<string>;
}
function operations(): AcceptanceOperations {
  return {
    command,
    fingerprint: worktreeFingerprint,
    artifact: artifactFingerprint,
    build: prepareBuild,
    scenario: checkedScenario,
    packageFile: () => readFile("package.json", "utf8"),
  };
}
function allowDirtyRun(args: string[]): boolean {
  const allowed = args.slice(2).length === 1 && args[2] === "--allow-dirty";
  if (args.length > 2 && !allowed)
    throw new Error("Usage: npm run test:live -- [--allow-dirty]");
  return allowed;
}
async function packageVersion(ops: AcceptanceOperations): Promise<string> {
  const pkg: unknown = JSON.parse(await ops.packageFile());
  if (
    !pkg ||
    typeof pkg !== "object" ||
    !("version" in pkg) ||
    typeof pkg.version !== "string"
  )
    throw new Error("Invalid package version");
  return pkg.version;
}
function verifyCompletion(
  record: LiveRecord,
  build: BuildResult,
  ops: AcceptanceOperations,
): void {
  if (record.fingerprint !== ops.fingerprint())
    throw new Error("Working tree changed during acceptance");
  if (record.artifact !== ops.artifact(build.root))
    throw new Error("Build artifact changed during acceptance");
  if (record.head !== ops.command("git", ["rev-parse", "HEAD"]))
    throw new Error("HEAD changed during acceptance");
}
export async function runAcceptance(
  record: LiveRecord,
  state: { build?: BuildResult },
  args: string[] = process.argv,
  ops: AcceptanceOperations = operations(),
): Promise<void> {
  const allowDirty = allowDirtyRun(args);
  record.head = ops.command("git", ["rev-parse", "HEAD"]);
  record.tree = ops.command("git", ["rev-parse", "HEAD^{tree}"]);
  const dirty = Boolean(
    ops.command("git", ["status", "--porcelain", "--untracked-files=normal"]),
  );
  record.dirty = allowDirty || dirty;
  if (dirty && !allowDirty)
    throw new Error(
      "Live acceptance requires a clean working tree (use --allow-dirty for development)",
    );
  record.fingerprint = ops.fingerprint();
  record.version = await packageVersion(ops);
  record.codex = ops.command("codex", ["--version"]);
  ops.command("codex", ["login", "status"]);
  ops.command("tmux", ["-V"]);
  state.build = await ops.build();
  record.artifact = state.build.artifact;
  record.node = state.build.node;
  record.npm = state.build.npm;
  if (record.fingerprint !== ops.fingerprint())
    throw new Error("Working tree changed during build");
  process.env.THC_LIVE_SYNTHETIC_TOKEN = "123:synthetic";
  const { acceptance } = await ops.scenario(state.build.root);
  verifyCompletion(record, state.build, ops);
  for (const adapter of ["protocol", "tmux"] as const) {
    await acceptance(adapter, state.build.root);
    record.adapters[adapter] = "passed";
  }
  verifyCompletion(record, state.build, ops);
  record.result = "passed";
}
export async function runLive(
  args: string[] = process.argv,
  ops: AcceptanceOperations = operations(),
): Promise<boolean> {
  const record = newRecord(),
    state: { build?: BuildResult } = {};
  await mkdir(".quality", { recursive: true });
  await writeFile(recordPath, `${JSON.stringify(record, null, 2)}\n`, {
    mode: 0o600,
  });
  let passed = false;
  try {
    await runAcceptance(record, state, args, ops);
    passed = true;
  } catch (error) {
    console.error(
      `Live acceptance failed: ${error instanceof Error ? error.message : "unknown error"}`,
    );
  } finally {
    if (state.build)
      await rm(state.build.root, { recursive: true, force: true });
    await writeFile(recordPath, `${JSON.stringify(record, null, 2)}\n`, {
      mode: 0o600,
    });
  }
  return passed;
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href)
  process.exitCode = (await runLive()) ? 0 : 1;
