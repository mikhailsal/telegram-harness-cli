import { spawnSync } from "node:child_process";
import { join, resolve } from "node:path";
import { expect, it } from "vitest";
import {
  validateRecord,
  artifactFingerprint,
  copyBuildInputs,
  worktreeFingerprint,
} from "../../scripts/release.ts";
import { newRecord } from "../../scripts/test-live.ts";

const expected = {
  head: "commit",
  tree: "tree",
  version: "1.2.3",
  fingerprint: "digest",
  artifact: "a".repeat(64),
  node: "v24",
  npm: "11",
  now: Date.parse("2026-01-02T00:00:00Z"),
};
function check(record: unknown): number {
  try {
    validateRecord(record, expected);
    return 0;
  } catch {
    return 1;
  }
}
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
it("accepts only a recent matching record with both adapters passed", () => {
  expect(check(passing)).toBe(0);
  expect(check({ ...passing, result: "failed" })).toBe(1);
  expect(check({ ...passing, dirty: true })).toBe(1);
  expect(check({ ...passing, fingerprint: "different" })).toBe(1);
  expect(check({ ...passing, schema: 1 })).toBe(1);
  expect(check({ ...passing, artifact: "b".repeat(64) })).toBe(1);
  expect(check({ ...passing, node: "v22" })).toBe(1);
  expect(check({ ...passing, npm: "10" })).toBe(1);
  expect(check({ ...passing, codex: "" })).toBe(1);
  expect(check({ ...passing, adapters: { protocol: "passed" } })).toBe(1);
  expect(check({ ...passing, head: "other" })).toBe(1);
  expect(check({ ...passing, tree: "other" })).toBe(1);
  expect(check({ ...passing, version: "other" })).toBe(1);
  expect(check({ ...passing, date: "2025-12-20T00:00:00Z" })).toBe(1);
  expect(check({ ...passing, date: "2026-01-03T00:00:00Z" })).toBe(1);
  expect(check(null)).toBe(1);
});

it("hashes both built files and installed dependencies", async () => {
  const { mkdir, mkdtemp, writeFile, rm, symlink } =
    await import("node:fs/promises");
  const { tmpdir } = await import("node:os");
  const root = await mkdtemp(join(tmpdir(), "thc-artifact-"));
  try {
    for (const dir of ["dist", "skills", "node_modules"]) {
      await mkdir(join(root, dir));
      await writeFile(join(root, dir, "file"), "original");
    }
    for (const file of ["package.json", "npm-shrinkwrap.json"])
      await writeFile(join(root, file), "original");
    const digest = () => artifactFingerprint(root);
    const initial = digest();
    await writeFile(join(root, "dist", "file"), "tampered");
    expect(digest()).not.toBe(initial);
    await writeFile(join(root, "dist", "file"), "original");
    await writeFile(join(root, "node_modules", "file"), "tampered");
    expect(digest()).not.toBe(initial);
    await symlink("file", join(root, "node_modules", "link"));
    expect(digest()).not.toBe(initial);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

it("copies only Git-enumerated build inputs into the isolated snapshot", async () => {
  const { mkdir, mkdtemp, readFile, rm, writeFile } =
    await import("node:fs/promises");
  const { tmpdir } = await import("node:os");
  const root = await mkdtemp(join(tmpdir(), "thc-inputs-"));
  const target = await mkdtemp(join(tmpdir(), "thc-inputs-copy-"));
  try {
    await mkdir(join(root, "src"));
    await writeFile(join(root, ".gitignore"), "src/ignored.ts\n.thc/\n");
    await writeFile(join(root, "src/included.ts"), "source");
    await writeFile(join(root, "src/ignored.ts"), "ignored");
    await mkdir(join(root, ".thc"));
    await writeFile(join(root, ".thc/secret"), "secret");
    expect(spawnSync("git", ["init", "-q"], { cwd: root }).status).toBe(0);
    await copyBuildInputs(target, root);
    expect(await readFile(join(target, "src/included.ts"), "utf8")).toBe(
      "source",
    );
    await expect(readFile(join(target, "src/ignored.ts"))).rejects.toThrow();
    await expect(readFile(join(target, ".thc/secret"))).rejects.toThrow();
  } finally {
    await rm(root, { recursive: true, force: true });
    await rm(target, { recursive: true, force: true });
  }
});

it("records an explicit failure when live prerequisites are unavailable", async () => {
  const { mkdtemp, readFile, rm } = await import("node:fs/promises");
  const { tmpdir } = await import("node:os");
  const root = await mkdtemp(join(tmpdir(), "thc-live-failure-"));
  try {
    const result = spawnSync(
      process.execPath,
      [resolve("scripts/test-live.ts")],
      { cwd: root, encoding: "utf8" },
    );
    expect(result.status).toBe(1);
    expect(result.stderr).toContain("Live acceptance failed");
    const record = JSON.parse(
      await readFile(join(root, ".quality", "test-live.json"), "utf8"),
    ) as { result: string };
    expect(record.result).toBe("failed");
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

async function setupFailureRepository(root: string): Promise<void> {
  const { mkdir, writeFile } = await import("node:fs/promises");
  await mkdir(join(root, "scripts"));
  await mkdir(join(root, "commands"));
  await mkdir(join(root, ".quality"));
  await writeFile(join(root, "package.json"), '{"version":"1.2.3"}');
  await writeFile(
    join(root, "scripts/build.ts"),
    'throw new Error("build failed")',
  );
  for (const name of ["codex", "tmux"])
    await writeFile(join(root, "commands", name), "#!/bin/sh\necho fake\n", {
      mode: 0o755,
    });
  expect(spawnSync("git", ["init", "-q"], { cwd: root }).status).toBe(0);
  expect(
    spawnSync(
      "git",
      [
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "--allow-empty",
        "-qm",
        "initial",
      ],
      { cwd: root },
    ).status,
  ).toBe(0);
}

it("invalidates an old passing record when isolated install or build fails", async () => {
  const { mkdtemp, readFile, rm, writeFile } = await import("node:fs/promises");
  const { tmpdir } = await import("node:os");
  const root = await mkdtemp(join(tmpdir(), "thc-build-failure-"));
  try {
    await setupFailureRepository(root);
    for (const exit of [1, 0]) {
      await writeFile(
        join(root, "commands/npm"),
        `#!/bin/sh\nexit ${String(exit)}\n`,
        {
          mode: 0o755,
        },
      );
      await writeFile(
        join(root, ".quality/test-live.json"),
        JSON.stringify(passing),
      );
      const result = spawnSync(
        process.execPath,
        [resolve("scripts/test-live.ts"), "--allow-dirty"],
        {
          cwd: root,
          encoding: "utf8",
          env: {
            ...process.env,
            PATH: `${join(root, "commands")}:${process.env.PATH ?? ""}`,
          },
        },
      );
      expect(result.status).toBe(1);
      const record = JSON.parse(
        await readFile(join(root, ".quality/test-live.json"), "utf8"),
      ) as { result: string; dirty: boolean };
      expect(record.result).toBe("failed");
      expect(record.dirty).toBe(true);
      expect(result.stderr).toContain(
        exit ? "npm ci failed" : "scripts/build.ts failed",
      );
    }
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

it("fingerprints untracked source changes without staging them", async () => {
  const { mkdtemp, writeFile, rm } = await import("node:fs/promises");
  const { tmpdir } = await import("node:os");
  const root = await mkdtemp(join(tmpdir(), "thc-fingerprint-"));
  try {
    const initialized = spawnSync("git", ["init", "--quiet"], { cwd: root });
    expect(initialized.status).toBe(0);
    await writeFile(join(root, "input.txt"), "first");
    const fingerprint = () => worktreeFingerprint(root);
    const first = fingerprint();
    await writeFile(join(root, "input.txt"), "second");
    expect(first).toMatch(/^[a-f0-9]{64}$/);
    expect(fingerprint()).not.toBe(first);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

it("fingerprints deleted tracked inputs and rejects symlinked build inputs", async () => {
  const { mkdir, mkdtemp, rm, symlink, writeFile } =
    await import("node:fs/promises");
  const { tmpdir } = await import("node:os");
  const root = await mkdtemp(join(tmpdir(), "thc-input-check-"));
  const target = await mkdtemp(join(tmpdir(), "thc-input-target-"));
  try {
    expect(() => worktreeFingerprint(root)).toThrow("git ls-files failed");
    expect(spawnSync("git", ["init", "-q"], { cwd: root }).status).toBe(0);
    await mkdir(join(root, "src"));
    await writeFile(join(root, "src/removed.ts"), "original");
    expect(
      spawnSync("git", ["add", "src/removed.ts"], { cwd: root }).status,
    ).toBe(0);
    const before = worktreeFingerprint(root);
    await rm(join(root, "src/removed.ts"));
    expect(worktreeFingerprint(root)).not.toBe(before);
    await copyBuildInputs(target, root);
    await symlink("missing", join(root, "src/link.ts"));
    expect(worktreeFingerprint(root)).not.toBe(before);
    await expect(copyBuildInputs(target, root)).rejects.toThrow(
      "Invalid build input",
    );
  } finally {
    await rm(root, { recursive: true, force: true });
    await rm(target, { recursive: true, force: true });
  }
});

it("starts live acceptance with a failure record", () => {
  expect(newRecord()).toMatchObject({
    result: "failed",
    dirty: true,
    adapters: {},
  });
});
