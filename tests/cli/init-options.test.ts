import { mkdtemp, mkdir, rm, symlink, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { expect, it } from "vitest";
import { parseArguments } from "../../src/cli/arguments.js";
import { Store } from "../../src/store/index.js";
import type { CliDependencies } from "../../src/cli/context.js";
import {
  alreadyRunning,
  configuredDirectory,
  installedTarget,
  reportCliPath,
} from "../../src/cli/init-options.js";

it("keeps the installed version unless an update or explicit target is requested", async () => {
  const dir = await mkdtemp(join(tmpdir(), "thc-init-options-"));
  try {
    const plain = parseArguments(["init"]);
    expect(await installedTarget(plain, dir, false)).toBeUndefined();
    expect(await installedTarget(plain, dir, true)).toBeUndefined();
    await mkdir(join(dir, ".thc"));
    await writeFile(join(dir, ".thc", "version"), "1.2.3\n");
    expect(await installedTarget(plain, dir, true)).toBe("1.2.3");
    expect(
      await installedTarget(parseArguments(["init", "--update"]), dir, true),
    ).toBeUndefined();
    expect(
      await installedTarget(
        parseArguments(["init", "--to", "2.0.0"]),
        dir,
        true,
      ),
    ).toBe("2.0.0");
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});
it("checks active services only on ordinary reruns and reports the CLI location when needed", async () => {
  const dir = await mkdtemp(join(tmpdir(), "thc-init-options-"));
  try {
    let output = "";
    const deps = {
      home: dir,
      env: {},
      stdout: (text: string) => {
        output += text;
      },
    } as CliDependencies;
    const active = () => Promise.resolve(true);
    expect(
      await alreadyRunning(parseArguments(["init"]), dir, deps, active),
    ).toBe(true);
    for (const option of ["--no-start", "--update", "--force"])
      expect(
        await alreadyRunning(
          parseArguments(["init", option]),
          dir,
          deps,
          active,
        ),
      ).toBe(false);
    expect(
      await alreadyRunning(
        parseArguments(["init", "--to", "1.0.0"]),
        dir,
        deps,
        active,
      ),
    ).toBe(false);
    expect(
      await alreadyRunning(parseArguments(["init"]), dir, deps, () =>
        Promise.resolve(false),
      ),
    ).toBe(false);
    reportCliPath(deps);
    expect(output).toContain(join(dir, ".local", "bin", "thc"));
    output = "";
    deps.env.PATH = join(dir, ".local", "bin");
    reportCliPath(deps);
    expect(output).toBe("");
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});

it("preserves a stored symlink identity only while it resolves to this project", async () => {
  const root = await mkdtemp(join(tmpdir(), "thc-legacy-alias-"));
  try {
    const directory = join(root, "project");
    const alias = join(root, "alias");
    const other = join(root, "other");
    await mkdir(join(directory, ".thc"), { recursive: true });
    await mkdir(other);
    await symlink(directory, alias);
    expect(await configuredDirectory(directory)).toBe(directory);
    const store = new Store(join(directory, ".thc", "store.db"));
    expect(await configuredDirectory(directory)).toBe(directory);
    store.putEntity({
      id: "entity",
      directory: alias,
      botId: "123",
      createdAt: "2026-01-01",
    });
    store.setCursor("entity-id", "entity");
    store.close();
    expect(await configuredDirectory(directory)).toBe(alias);
    await rm(alias);
    expect(await configuredDirectory(directory)).toBe(directory);
    await symlink(other, alias);
    expect(await configuredDirectory(directory)).toBe(directory);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
