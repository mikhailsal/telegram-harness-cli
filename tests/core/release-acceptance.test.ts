import { join, resolve } from "node:path";
import { expect, it, vi } from "vitest";
import {
  newRecord,
  runAcceptance,
  runLive,
  checkedScenario,
} from "../../scripts/test-live.ts";
import type { AcceptanceOperations } from "../../scripts/test-live.ts";
import { artifactFingerprint } from "../../scripts/release.ts";

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
function acceptanceOperations(): AcceptanceOperations {
  return {
    command: (program, args) =>
      program !== "git"
        ? "installed"
        : args[0] === "status"
          ? ""
          : args[1] === "HEAD^{tree}"
            ? "tree"
            : "commit",
    fingerprint: () => "source",
    artifact: () => "artifact",
    build: () =>
      Promise.resolve({
        root: "/tmp/synthetic",
        artifact: "artifact",
        node: "v24",
        npm: "11",
      }),
    scenario: () => Promise.resolve({ acceptance: () => Promise.resolve() }),
    packageFile: () => Promise.resolve('{"version":"1.2.3"}'),
  };
}
it("accepts only both adapters against the unchanged isolated artifact", async () => {
  const record = newRecord();
  const ops = acceptanceOperations();
  const adapters: string[] = [];
  ops.scenario = () =>
    Promise.resolve({
      acceptance: (adapter) => {
        adapters.push(adapter);
        return Promise.resolve();
      },
    });
  await runAcceptance(record, {}, ["node", "script"], ops);
  expect(record).toMatchObject({
    result: "passed",
    dirty: false,
    fingerprint: "source",
    artifact: "artifact",
    adapters: { protocol: "passed", tmux: "passed" },
  });
  expect(adapters).toEqual(["protocol", "tmux"]);
  const dirty = newRecord();
  await runAcceptance(dirty, {}, ["node", "script", "--allow-dirty"], ops);
  expect(dirty.dirty).toBe(true);
});

it("rejects changed sources, artifact, dirty trees, and failed adapters", async () => {
  const args = ["node", "script"];
  const changed = acceptanceOperations();
  changed.fingerprint = (() => {
    let count = 0;
    return () => (++count < 3 ? "source" : "changed");
  })();
  await expect(runAcceptance(newRecord(), {}, args, changed)).rejects.toThrow(
    "Working tree changed",
  );
  const artifact = acceptanceOperations();
  artifact.artifact = () => "changed";
  const neverRun = vi.fn().mockResolvedValue(undefined);
  artifact.scenario = () => Promise.resolve({ acceptance: neverRun });
  await expect(runAcceptance(newRecord(), {}, args, artifact)).rejects.toThrow(
    "Build artifact changed",
  );
  expect(neverRun).not.toHaveBeenCalled();
  const dirty = acceptanceOperations();
  const command = dirty.command;
  dirty.command = (program, argv) =>
    argv[0] === "status" ? "dirty" : command(program, argv);
  await expect(runAcceptance(newRecord(), {}, args, dirty)).rejects.toThrow(
    "clean working tree",
  );
  const failure = acceptanceOperations();
  failure.scenario = () =>
    Promise.resolve({
      acceptance: () => Promise.reject(new Error("synthetic failure")),
    });
  await expect(runAcceptance(newRecord(), {}, args, failure)).rejects.toThrow(
    "synthetic failure",
  );
  await expect(
    runAcceptance(newRecord(), {}, [...args, "--unknown"], failure),
  ).rejects.toThrow("Usage");
  const invalid = acceptanceOperations();
  invalid.packageFile = () => Promise.resolve("{}");
  await expect(runAcceptance(newRecord(), {}, args, invalid)).rejects.toThrow(
    "Invalid package version",
  );
});

it("writes the live record after success or failure and removes the temporary build", async () => {
  const { mkdtemp, mkdir, readFile, rm, writeFile } =
    await import("node:fs/promises");
  const { tmpdir } = await import("node:os");
  const root = await mkdtemp(join(tmpdir(), "thc-record-"));
  const prior = process.cwd();
  const error = vi.spyOn(console, "error").mockImplementation(() => undefined);
  try {
    process.chdir(root);
    await mkdir(".quality");
    await writeFile(".quality/test-live.json", JSON.stringify(passing));
    expect(await runLive(["node", "script", "--invalid"])).toBe(false);
    expect(
      JSON.parse(await readFile(".quality/test-live.json", "utf8")),
    ).toMatchObject({ result: "failed" });
    const ops = acceptanceOperations();
    const buildRoot = join(root, "temporary-build");
    await mkdir(buildRoot);
    ops.build = () =>
      Promise.resolve({
        root: buildRoot,
        artifact: "artifact",
        node: "v24",
        npm: "11",
      });
    expect(await runLive(["node", "script"], ops)).toBe(true);
    expect(
      JSON.parse(await readFile(".quality/test-live.json", "utf8")),
    ).toMatchObject({ result: "passed" });
    await expect(readFile(buildRoot)).rejects.toThrow();
  } finally {
    process.chdir(prior);
    error.mockRestore();
    await rm(root, { recursive: true, force: true });
  }
});

async function initializeArtifact(root: string): Promise<void> {
  const { mkdir, writeFile } = await import("node:fs/promises");
  await mkdir(join(root, "dist"));
  await mkdir(join(root, "skills"));
  await writeFile(join(root, "package.json"), '{"type":"module"}');
  await writeFile(join(root, "npm-shrinkwrap.json"), "{}");
}
async function copyInstalledModules(root: string): Promise<void> {
  const { cp, mkdir, readdir } = await import("node:fs/promises");
  const { constants } = await import("node:fs");
  const source = resolve("node_modules");
  const destination = join(root, "node_modules");
  await mkdir(destination);
  const results = await Promise.allSettled(
    (await readdir(source)).map((name) =>
      cp(join(source, name), join(destination, name), {
        recursive: true,
        mode: constants.COPYFILE_FICLONE,
      }),
    ),
  );
  const failures = results.filter((result) => result.status === "rejected");
  if (failures.length)
    throw new AggregateError(
      failures.map((failure): unknown => failure.reason),
      "Failed to clone installed dependencies",
    );
}
it("checks live scenarios without modifying the isolated artifact", async () => {
  const { cp, mkdtemp, rm } = await import("node:fs/promises");
  const { tmpdir } = await import("node:os");
  const root = await mkdtemp(join(tmpdir(), "thc-live-checks-"));
  try {
    await initializeArtifact(root);
    await cp(resolve("src"), join(root, "src"), { recursive: true });
    await copyInstalledModules(root);
    await rm(join(root, "node_modules/.vite-temp"), {
      recursive: true,
      force: true,
    });
    await rm(join(root, "node_modules/.vitest-cache"), {
      recursive: true,
      force: true,
    });
    const before = artifactFingerprint(root);
    expect((await checkedScenario(root)).acceptance).toBeTypeOf("function");
    expect(artifactFingerprint(root)).toBe(before);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
}, 60_000);

it("rejects a self-check that mutates installed dependencies before loading the scenario", async () => {
  const { mkdir, mkdtemp, rm, writeFile } = await import("node:fs/promises");
  const { tmpdir } = await import("node:os");
  const root = await mkdtemp(join(tmpdir(), "thc-live-tamper-"));
  try {
    await initializeArtifact(root);
    await mkdir(join(root, "node_modules/vitest"), { recursive: true });
    await writeFile(
      join(root, "node_modules/vitest/vitest.mjs"),
      `import { writeFileSync } from "node:fs"; import { join } from "node:path"; const root=process.argv[process.argv.indexOf("--root")+1]; writeFileSync(join(root,"node_modules","tampered"),"x");`,
    );
    await expect(checkedScenario(root)).rejects.toThrow(
      "Build artifact changed during live self-check",
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
