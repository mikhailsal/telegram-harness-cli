import { existsSync } from "node:fs";
import { mkdtemp, mkdir, rm, symlink, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { expect, it } from "vitest";
import { parseArguments } from "../../src/cli/arguments.js";
import { configSchema } from "../../src/core/config.js";
import { SCHEMA_VERSION, Store } from "../../src/store/index.js";
import type { CliDependencies } from "../../src/cli/context.js";
import { CliError } from "../../src/cli/errors.js";
import {
  alreadyRunning,
  assertSettingsMatch,
  configuredDirectory,
  installedTarget,
  markFirstRun,
  onboardingFor,
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

function mismatch(
  argv: string[],
  saved: ReturnType<typeof configSchema.parse>,
) {
  try {
    assertSettingsMatch(parseArguments(argv), saved, "/projects/app");
  } catch (error) {
    return error;
  }
  return undefined;
}

it("accepts matching explicit settings and names every contradicting flag", () => {
  const saved = configSchema.parse({
    token: { file: "tokens/bot" },
    allowlist: [123, 456],
    sandbox: "read-only",
  });
  expect(
    mismatch(
      [
        "init",
        "--sandbox",
        "read-only",
        "--user-id",
        "456",
        "--token-file",
        "/projects/app/tokens/bot",
      ],
      saved,
    ),
  ).toBeUndefined();
  expect(mismatch(["init", "--yes"], saved)).toBeUndefined();
  const error = mismatch(
    [
      "init",
      "--sandbox",
      "danger-full-access",
      "--user-id",
      "not-a-number",
      "--token-file",
      "/elsewhere/token",
    ],
    saved,
  );
  expect(error).toBeInstanceOf(CliError);
  if (!(error instanceof CliError)) throw new Error("Expected CliError");
  expect(error.exitCode).toBe(3);
  expect(error.info).toMatchObject({
    code: "E_CONFIG",
    what: "--sandbox, --user-id, --token-file would change the saved configuration of /projects/app",
  });
  expect(error.info.fix).toContain("/projects/app/.thc/config.json");
  // A running bridge reads its configuration only at start, so the hint must
  // say how to apply an edit instead of implying that `init` alone does it.
  expect(error.info.fix).toContain("thc stop");
  expect(error.info.fix).toContain("thc init");
});

it("opens the project store only for a version that uses this executable's schema", async () => {
  const dir = await mkdtemp(join(tmpdir(), "thc-init-options-"));
  try {
    const installed = join(dir, ".local", "share", "thc");
    for (const [name, schema] of [
      ["1.0.0", SCHEMA_VERSION],
      ["0.9.0", SCHEMA_VERSION - 1],
    ] as const) {
      await mkdir(join(installed, name), { recursive: true });
      await writeFile(
        join(installed, name, "version.json"),
        JSON.stringify({ version: name, schema }),
      );
    }
    expect(await onboardingFor(dir, "1.0.0", false)).toBe("new");
    expect(await onboardingFor(dir, "1.0.0", true)).toBe("resume");
    expect(await onboardingFor(dir, "0.9.0", false)).toBe("skip");
    expect(await onboardingFor(dir, "0.9.0", true)).toBe("skip");

    await mkdir(join(dir, ".thc"));
    const database = join(dir, ".thc", "store.db");
    expect(markFirstRun(dir, "skip", "2026-01-01T00:00:00.000Z")).toBe(false);
    expect(existsSync(database)).toBe(false);
    expect(markFirstRun(dir, "new", "2026-01-01T00:00:00.000Z")).toBe(true);
    // Saved but never answered: the next run still waits for a first answer.
    expect(markFirstRun(dir, "resume", "2026-01-02T00:00:00.000Z")).toBe(true);
    const store = new Store(database);
    store.setCursor("first-delivery-at", "2026-01-03T00:00:00.000Z");
    store.close();
    expect(markFirstRun(dir, "resume", "2026-01-04T00:00:00.000Z")).toBe(false);
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});

it("treats an environment token as different from any token file", () => {
  const saved = configSchema.parse({
    token: { env: "BOT" },
    allowlist: [123],
    sandbox: "read-only",
  });
  expect(mismatch(["init", "--user-id", "123"], saved)).toBeUndefined();
  const error = mismatch(["init", "--token-file", "/tokens/bot"], saved);
  expect(error).toBeInstanceOf(CliError);
  expect(String(error)).toContain("--token-file would change");
});
