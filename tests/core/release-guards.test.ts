import { spawnSync } from "node:child_process";
import { join } from "node:path";
import { expect, it } from "vitest";
import {
  prepareBuild,
  toolchain,
  createRelease,
  verifyTag,
  releaseMain,
  worktreeFingerprint,
} from "../../scripts/release.ts";

const passing = {
  schema: 2,
  result: "passed",
  dirty: false,
  fingerprint: "digest",
  artifact: "a".repeat(64),
  node: "v24",
  npm: "11",
  head: "commit",
  tree: "tree",
  version: "1.2.3",
  codex: "codex 1",
  date: "2026-01-01T23:00:00Z",
  adapters: { protocol: "passed", tmux: "passed" },
};
async function releaseRepository(root: string): Promise<void> {
  const { mkdir, writeFile } = await import("node:fs/promises");
  for (const directory of ["scripts", "src", "skills", ".commands", ".quality"])
    await mkdir(join(root, directory));
  await writeFile(
    join(root, ".gitignore"),
    ".quality/\n.commands/\nnode_modules/\ndist/\nnpm-shrinkwrap.json\n",
  );
  await writeFile(join(root, "package.json"), '{"version":"1.2.3"}');
  await writeFile(join(root, "package-lock.json"), "locked");
  await writeFile(join(root, "src/input.ts"), "export const value = 1;\n");
  await writeFile(join(root, "skills/SKILL.md"), "skill\n");
  await writeFile(
    join(root, "scripts/build.ts"),
    'import {mkdirSync, writeFileSync} from "node:fs"; mkdirSync("dist"); writeFileSync("dist/output.js", "built"); writeFileSync("npm-shrinkwrap.json", "locked");\n',
  );
  await writeFile(
    join(root, ".commands/npm"),
    '#!/bin/sh\nif [ "$1" = "--version" ]; then echo 11; else mkdir node_modules; fi\n',
    { mode: 0o755 },
  );
  expect(spawnSync("git", ["init", "-q"], { cwd: root }).status).toBe(0);
  expect(
    spawnSync("git", ["config", "user.name", "Test"], { cwd: root }).status,
  ).toBe(0);
  expect(
    spawnSync("git", ["config", "user.email", "test@example.com"], {
      cwd: root,
    }).status,
  ).toBe(0);
  expect(spawnSync("git", ["add", "."], { cwd: root }).status).toBe(0);
  expect(
    spawnSync(
      "git",
      [
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-qm",
        "initial",
      ],
      { cwd: root },
    ).status,
  ).toBe(0);
}
async function releaseFailureCases(root: string): Promise<void> {
  const { readFile, writeFile } = await import("node:fs/promises");
  const packagePath = join(root, "package.json");
  const originalPackage = await readFile(packagePath, "utf8");
  await writeFile(packagePath, '{"version":"invalid"}');
  await expect(createRelease()).rejects.toThrow("Invalid package version");
  await writeFile(packagePath, originalPackage);
  const source = join(root, "src/input.ts");
  await writeFile(source, "changed");
  await expect(createRelease()).rejects.toThrow("clean working tree");
  await writeFile(source, "export const value = 1;\n");
  const npmPath = join(root, ".commands/npm");
  const npm = await readFile(npmPath, "utf8");
  await writeFile(npmPath, "#!/bin/sh\nexit 1\n");
  expect(() => toolchain()).toThrow("npm version unavailable");
  await expect(prepareBuild()).rejects.toThrow("npm ci failed");
  await writeFile(npmPath, npm);
}
function gitOutput(root: string, ...args: string[]): string {
  const result = spawnSync("git", args, { cwd: root, encoding: "utf8" });
  if (result.status !== 0) throw new Error("fixture git failed");
  return result.stdout.trim();
}
async function rejectedTags(root: string): Promise<void> {
  gitOutput(root, "tag", "v1.2.3");
  await expect(verifyTag("v1.2.3")).rejects.toThrow("must be annotated");
  gitOutput(root, "tag", "-d", "v1.2.3");
  gitOutput(
    root,
    "-c",
    "user.name=Test",
    "-c",
    "user.email=test@example.com",
    "tag",
    "-a",
    "v1.2.3",
    "-m",
    "invalid record",
  );
  await expect(verifyTag("v1.2.3")).rejects.toThrow("does not contain");
  gitOutput(root, "tag", "-d", "v1.2.3");
}
it("rebuilds and verifies an annotated release in an isolated repository", async () => {
  const { mkdtemp, rm, writeFile } = await import("node:fs/promises");
  const { tmpdir } = await import("node:os");
  const root = await mkdtemp(join(tmpdir(), "thc-release-"));
  const prior = process.cwd(),
    path = process.env.PATH;
  try {
    await releaseRepository(root);
    process.chdir(root);
    process.env.PATH = `${join(root, ".commands")}:${path ?? ""}`;
    await releaseFailureCases(root);
    await rejectedTags(root);
    const build = await prepareBuild();
    await rm(build.root, { recursive: true, force: true });
    const record = {
      ...passing,
      ...build,
      date: new Date().toISOString(),
      head: gitOutput(root, "rev-parse", "HEAD"),
      tree: gitOutput(root, "rev-parse", "HEAD^{tree}"),
      fingerprint: worktreeFingerprint(root),
    };
    await writeFile(
      join(root, ".quality/test-live.json"),
      JSON.stringify(record),
    );
    await releaseMain(["node", "script"]);
    await releaseMain(["node", "script", "--verify-tag", "v1.2.3"]);
    await expect(verifyTag("v1.2.4")).rejects.toThrow("Tag does not match");
    await expect(releaseMain(["node", "script", "--bad"])).rejects.toThrow(
      "Usage",
    );
    gitOutput(
      root,
      "-c",
      "user.name=Test",
      "-c",
      "user.email=test@example.com",
      "commit",
      "--allow-empty",
      "-qm",
      "next",
    );
    await expect(verifyTag("v1.2.3")).rejects.toThrow("does not point");
  } finally {
    process.chdir(prior);
    if (path === undefined) delete process.env.PATH;
    else process.env.PATH = path;
    await rm(root, { recursive: true, force: true });
  }
});
