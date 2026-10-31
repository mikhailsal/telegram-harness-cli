import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { lstatSync, readFileSync, readlinkSync, readdirSync } from "node:fs";
import { cp, mkdir, mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { pathToFileURL } from "node:url";

const MAX_AGE = 24 * 60 * 60 * 1000;
export interface BuildResult {
  root: string;
  artifact: string;
  node: string;
  npm: string;
}
export interface RecordContext {
  head: string;
  tree: string;
  version: string;
  fingerprint: string;
  artifact: string;
  node: string;
  npm: string;
  now: number;
}
export interface LiveRecord extends Omit<RecordContext, "now"> {
  schema: number;
  date: string;
  codex: string;
  adapters: Partial<Record<"protocol" | "tmux", string>>;
  result: string;
  dirty: boolean;
}
function git(args: string[], input?: string, cwd?: string): string {
  const result = spawnSync("git", args, {
    encoding: "utf8",
    ...(cwd === undefined ? {} : { cwd }),
    ...(input === undefined ? {} : { input }),
  });
  if (result.error || result.status !== 0)
    throw new Error(`git ${args[0] ?? ""} failed`);
  return result.stdout.trim();
}
export function worktreeFingerprint(cwd = process.cwd()): string {
  const names = [
    ...new Set(
      git(
        ["ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        undefined,
        cwd,
      )
        .split("\0")
        .filter(Boolean),
    ),
  ].sort();
  const hash = createHash("sha256");
  for (const name of names) {
    hash.update(name).update("\0");
    try {
      const path = join(cwd, name);
      const info = lstatSync(path);
      if (info.isSymbolicLink())
        hash.update("link\0").update(readlinkSync(path));
      else if (info.isFile())
        hash
          .update(info.mode & 0o111 ? "executable\0" : "file\0")
          .update(readFileSync(path));
      else hash.update("other");
    } catch (error) {
      if (
        !(error instanceof Error) ||
        !("code" in error) ||
        error.code !== "ENOENT"
      )
        throw error;
      hash.update("missing");
    }
    hash.update("\0");
  }
  return hash.digest("hex");
}
const buildRoots = ["src/", "skills/"];
const buildFiles = new Set([
  "package.json",
  "package-lock.json",
  "tsconfig.json",
  "tsconfig.build.json",
  "scripts/build.ts",
]);
export async function copyBuildInputs(
  destination: string,
  cwd = process.cwd(),
): Promise<void> {
  const names = git(
    ["ls-files", "-z", "--cached", "--others", "--exclude-standard"],
    undefined,
    cwd,
  )
    .split("\0")
    .filter(Boolean);
  for (const name of new Set(names)) {
    if (
      !buildFiles.has(name) &&
      !buildRoots.some((root) => name.startsWith(root))
    )
      continue;
    let info;
    try {
      info = lstatSync(join(cwd, name));
    } catch (error) {
      if (error instanceof Error && "code" in error && error.code === "ENOENT")
        continue;
      throw error;
    }
    if (!info.isFile()) throw new Error(`Invalid build input: ${name}`);
    const target = join(destination, name);
    await mkdir(dirname(target), { recursive: true });
    await cp(join(cwd, name), target);
  }
}
export function artifactFingerprint(root: string): string {
  const hash = createHash("sha256");
  function add(name: string): void {
    const path = join(root, name),
      info = lstatSync(path);
    hash.update(name).update("\0");
    if (info.isDirectory()) {
      hash.update("directory\0");
      for (const child of readdirSync(path).sort()) add(`${name}/${child}`);
    } else if (info.isSymbolicLink())
      hash.update("link\0").update(readlinkSync(path));
    else if (info.isFile())
      hash
        .update(info.mode & 0o111 ? "executable\0" : "file\0")
        .update(readFileSync(path));
    else throw new Error(`Invalid build artifact: ${name}`);
    hash.update("\0");
  }
  for (const name of [
    "dist",
    "skills",
    "node_modules",
    "package.json",
    "npm-shrinkwrap.json",
  ])
    add(name);
  return hash.digest("hex");
}
function runBuildCommand(program: string, args: string[], cwd: string): void {
  const result = spawnSync(program, args, { cwd, stdio: "inherit" });
  if (result.error || result.status !== 0)
    throw new Error(`${program} ${args[0] ?? ""} failed`);
}
export function toolchain(): { node: string; npm: string } {
  const result = spawnSync("npm", ["--version"], { encoding: "utf8" });
  if (result.error || result.status !== 0)
    throw new Error("npm version unavailable");
  return { node: process.version, npm: result.stdout.trim() };
}
export async function prepareBuild(): Promise<BuildResult> {
  const root = await mkdtemp(join(tmpdir(), "thc-build-"));
  try {
    await copyBuildInputs(root);
    runBuildCommand("npm", ["ci"], root);
    runBuildCommand(process.execPath, ["scripts/build.ts"], root);
    return { root, artifact: artifactFingerprint(root), ...toolchain() };
  } catch (error) {
    await rm(root, { recursive: true, force: true });
    throw error;
  }
}
export function validateRecord(value: unknown, expected: RecordContext): void {
  if (!value || typeof value !== "object")
    throw new Error("Missing live acceptance record");
  const record = value as Partial<LiveRecord>;
  if (record.dirty !== false)
    throw new Error("Dirty live runs cannot authorize a release");
  validateAdapters(record);
  validateIdentity(record, expected);
  validateArtifact(record, expected);
  const at = typeof record.date === "string" ? Date.parse(record.date) : NaN;
  if (!Number.isFinite(at) || at > expected.now || expected.now - at > MAX_AGE)
    throw new Error("Live acceptance record is stale or dated in the future");
}
function validateAdapters(record: Partial<LiveRecord>): void {
  if (
    record.result !== "passed" ||
    record.adapters?.protocol !== "passed" ||
    record.adapters.tmux !== "passed"
  )
    throw new Error("Both live adapters must pass");
}
function validateIdentity(
  record: Partial<LiveRecord>,
  expected: RecordContext,
): void {
  if (
    record.head !== expected.head ||
    record.tree !== expected.tree ||
    record.version !== expected.version ||
    typeof record.fingerprint !== "string" ||
    record.fingerprint !== expected.fingerprint
  )
    throw new Error("Live record does not match this commit and version");
  if (typeof record.codex !== "string" || !record.codex.trim())
    throw new Error("Missing Codex version");
}
function validateArtifact(
  record: Partial<LiveRecord>,
  expected: RecordContext,
): void {
  if (
    record.schema !== 2 ||
    !/^[a-f0-9]{64}$/.test(record.artifact ?? "") ||
    record.artifact !== expected.artifact ||
    record.node !== expected.node ||
    record.npm !== expected.npm
  )
    throw new Error(
      "Live record does not match the rebuilt artifact and toolchain",
    );
}
async function context(): Promise<
  Omit<RecordContext, "artifact" | "node" | "npm" | "now">
> {
  const pkg: unknown = JSON.parse(await readFile("package.json", "utf8"));
  if (
    !pkg ||
    typeof pkg !== "object" ||
    !("version" in pkg) ||
    typeof pkg.version !== "string" ||
    !/^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$/.test(pkg.version)
  )
    throw new Error("Invalid package version");
  return {
    head: git(["rev-parse", "HEAD"]),
    tree: git(["rev-parse", "HEAD^{tree}"]),
    version: pkg.version,
    fingerprint: worktreeFingerprint(),
  };
}
export async function createRelease(): Promise<void> {
  const expected = await context();
  if (git(["status", "--porcelain", "--untracked-files=normal"]))
    throw new Error("Release requires a clean working tree");
  const record: unknown = JSON.parse(
    await readFile(".quality/test-live.json", "utf8"),
  );
  const build = await prepareBuild();
  try {
    if (expected.fingerprint !== worktreeFingerprint())
      throw new Error("Working tree changed during build");
    validateRecord(record, { ...expected, ...build, now: Date.now() });
  } finally {
    await rm(build.root, { recursive: true, force: true });
  }
  const tag = `v${expected.version}`;
  const message = `THC-RELEASE-RECORD\n${JSON.stringify(record)}\n`;
  git(["tag", "-a", tag, "-F", "-"], message);
  console.log(`Created annotated tag ${tag}; it has not been pushed.`);
}
export async function verifyTag(tag: string): Promise<void> {
  const expected = await context();
  if (tag !== `v${expected.version}`)
    throw new Error("Tag does not match package version");
  if (git(["cat-file", "-t", `refs/tags/${tag}`]) !== "tag")
    throw new Error("Release tag must be annotated");
  if (git(["rev-list", "-n", "1", tag]) !== expected.head)
    throw new Error("Tag does not point to checked-out commit");
  const annotation = git([
    "for-each-ref",
    "--format=%(contents)",
    `refs/tags/${tag}`,
  ]);
  const prefix = "THC-RELEASE-RECORD\n";
  if (!annotation.startsWith(prefix))
    throw new Error("Tag does not contain a live acceptance record");
  const tagTime =
    Number(
      git(["for-each-ref", "--format=%(taggerdate:unix)", `refs/tags/${tag}`]),
    ) * 1000;
  const build = await prepareBuild();
  try {
    if (expected.fingerprint !== worktreeFingerprint())
      throw new Error("Working tree changed during build");
    validateRecord(JSON.parse(annotation.slice(prefix.length)), {
      ...expected,
      ...build,
      now: tagTime + 999,
    });
  } finally {
    await rm(build.root, { recursive: true, force: true });
  }
  console.log("Release tag acceptance record is valid.");
}
export async function releaseMain(
  args: string[] = process.argv,
): Promise<void> {
  if (args[2] === "--verify-tag" && args.length === 4 && args[3])
    await verifyTag(args[3]);
  else if (args.length === 2) await createRelease();
  else throw new Error("Usage: node scripts/release.ts [--verify-tag TAG]");
}
if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(process.argv[1]).href
) {
  try {
    await releaseMain();
  } catch (error) {
    console.error(
      `Release refused: ${error instanceof Error ? error.message : "unknown error"}`,
    );
    process.exitCode = 1;
  }
}
