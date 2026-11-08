import {
  mkdtemp,
  mkdir,
  readFile,
  readdir,
  rm,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, expect, it } from "vitest";
import { install, registerFolder } from "../../src/cli/install.js";

const roots: string[] = [];
afterEach(async () => {
  await Promise.all(
    roots.splice(0).map((root) => rm(root, { recursive: true, force: true })),
  );
});

it("initializes two projects concurrently with one shared package installation and both registrations", async () => {
  const root = await mkdtemp(join(tmpdir(), "thc-concurrent-install-"));
  roots.push(root);
  const home = join(root, "home");
  const pkg = join(root, "package");
  await mkdir(join(pkg, "dist", "cli"), { recursive: true });
  await mkdir(join(pkg, "skills"));
  await writeFile(join(pkg, "package.json"), "{}");
  await writeFile(join(pkg, "package-lock.json"), "{}");
  await writeFile(join(pkg, "dist", "cli", "main.js"), "export {};");
  let installations = 0;
  const exec = async () => {
    installations++;
    await new Promise((resolve) => setTimeout(resolve, 30));
    return { stdout: "", stderr: "" };
  };
  const projects = [join(root, "first"), join(root, "second")];
  await Promise.all(
    projects.map(async (directory) => {
      await mkdir(directory);
      const version = await install(
        { home, directory, packageRoot: pkg, exec },
        "1.0.0",
      );
      await registerFolder(home, directory, version);
    }),
  );
  expect(installations).toBe(1);
  const share = join(home, ".local", "share", "thc");
  expect(JSON.parse(await readFile(join(share, "folders"), "utf8"))).toEqual({
    [projects[0] ?? ""]: "1.0.0",
    [projects[1] ?? ""]: "1.0.0",
  });
  expect(
    (await readdir(share)).filter((name) => name.startsWith(".install-")),
  ).toEqual([]);
  for (const project of projects)
    expect(await readFile(join(project, ".thc", "version"), "utf8")).toBe(
      "1.0.0\n",
    );
});
