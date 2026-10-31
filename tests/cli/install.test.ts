import {
  mkdtempSync,
  mkdirSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { afterEach, beforeEach, expect, it } from "vitest";
import { install } from "../../src/cli/install.js";
import { SCHEMA_VERSION } from "../../src/store/index.js";

let root: string;
let home: string;
let directory: string;
let pkg: string;
beforeEach(() => {
  root = mkdtempSync(join(tmpdir(), "thc-install-"));
  home = join(root, "home");
  directory = join(root, "agent");
  pkg = join(root, "package");
  for (const path of [
    join(directory, ".thc"),
    join(pkg, "dist", "cli"),
    join(pkg, "skills"),
    join(pkg, "node_modules"),
  ])
    mkdirSync(path, { recursive: true });
  writeFileSync(join(pkg, "package.json"), "{}");
  writeFileSync(join(pkg, "package-lock.json"), "{}");
  writeFileSync(join(pkg, "dist", "cli", "main.js"), "export {};");
});
afterEach(() => {
  rmSync(root, { recursive: true, force: true });
});

it("leaves the selected version untouched when locked dependency installation fails", async () => {
  writeFileSync(join(directory, ".thc", "version"), "1.0.0\n");
  const exec = (file: string, args: string[], options?: { cwd?: string }) => {
    expect(file).toBe("npm");
    expect(args).toEqual(["ci", "--omit=dev"]);
    expect(
      readFileSync(join(options?.cwd ?? "", "package-lock.json"), "utf8"),
    ).toBe("{}");
    return Promise.reject(new Error("offline"));
  };
  await expect(
    install({ home, directory, packageRoot: pkg, exec }, "2.0.0"),
  ).rejects.toThrow("offline");
  expect(readFileSync(join(directory, ".thc", "version"), "utf8")).toBe(
    "1.0.0\n",
  );
  const { readdirSync } = await import("node:fs");
  expect(readdirSync(join(home, ".local", "share", "thc"))).toEqual([]);
});
it("refuses cross-schema rollback before altering the folder version", async () => {
  const current = new DatabaseSync(join(directory, ".thc", "store.db"));
  current.exec(`PRAGMA user_version=${String(SCHEMA_VERSION)}`);
  current.close();
  writeFileSync(join(directory, ".thc", "version"), "2.0.0\n");
  const old = join(home, ".local", "share", "thc", "1.0.0");
  mkdirSync(old, { recursive: true });
  writeFileSync(
    join(old, "version.json"),
    JSON.stringify({ version: "1.0.0", schema: SCHEMA_VERSION - 1 }),
  );
  await expect(
    install({ home, directory, packageRoot: pkg }, "2.0.0", "1.0.0"),
  ).rejects.toMatchObject({ info: { code: "E_STORE_SCHEMA_NEWER" } });
  expect(readFileSync(join(directory, ".thc", "version"), "utf8")).toBe(
    "2.0.0\n",
  );
});
it("selects an already installed compatible rollback version", async () => {
  const old = join(home, ".local", "share", "thc", "1.0.0");
  mkdirSync(old, { recursive: true });
  writeFileSync(
    join(old, "version.json"),
    JSON.stringify({ version: "1.0.0", schema: SCHEMA_VERSION }),
  );
  mkdirSync(join(old, "dist", "cli"), { recursive: true });
  writeFileSync(join(old, "dist", "cli", "main.js"), "export {};");
  writeFileSync(join(old, "package.json"), "{}");
  expect(
    await install({ home, directory, packageRoot: pkg }, "2.0.0", "1.0.0"),
  ).toBe("1.0.0");
  expect(readFileSync(join(directory, ".thc", "version"), "utf8").trim()).toBe(
    "1.0.0",
  );
});

it("backs up the current database before selecting a compatible release", async () => {
  const db = new DatabaseSync(join(directory, ".thc", "store.db"));
  db.exec(
    `CREATE TABLE witness(value TEXT); INSERT INTO witness VALUES ('kept'); PRAGMA user_version=${String(SCHEMA_VERSION)}`,
  );
  db.close();
  expect(
    await install(
      {
        home,
        directory,
        packageRoot: pkg,
        exec: () => Promise.resolve({ stdout: "", stderr: "" }),
      },
      "2.0.0",
    ),
  ).toBe("2.0.0");
  const { readdirSync } = await import("node:fs");
  const backupName = readdirSync(join(directory, ".thc")).find((name) =>
    name.startsWith("store-backup-"),
  );
  expect(backupName).toBeDefined();
  const saved = new DatabaseSync(join(directory, ".thc", backupName ?? ""), {
    readOnly: true,
  });
  try {
    expect(saved.prepare("SELECT value FROM witness").get()).toMatchObject({
      value: "kept",
    });
  } finally {
    saved.close();
  }
});
it("refuses an incomplete installed version without changing the folder", async () => {
  const old = join(home, ".local", "share", "thc", "1.0.0");
  mkdirSync(old, { recursive: true });
  writeFileSync(
    join(old, "version.json"),
    JSON.stringify({ version: "1.0.0", schema: SCHEMA_VERSION }),
  );
  await expect(
    install({ home, directory, packageRoot: pkg }, "2.0.0", "1.0.0"),
  ).rejects.toMatchObject({ info: { code: "E_DEPENDENCY" } });
  expect(() => readFileSync(join(directory, ".thc", "version"))).toThrow();
});

it("routes the shim by explicit folder and prefers a stable latest version outside folders", async () => {
  await install(
    {
      home,
      directory,
      packageRoot: pkg,
      exec: () => Promise.resolve({ stdout: "", stderr: "" }),
    },
    "2.0.0",
  );
  const share = join(home, ".local", "share", "thc");
  const other = join(share, "1.0.0", "dist", "cli");
  const prerelease = join(share, "2.0.0-beta", "dist", "cli");
  mkdirSync(other, { recursive: true });
  mkdirSync(prerelease, { recursive: true });
  writeFileSync(join(other, "main.js"), "console.log('folder-version')");
  writeFileSync(
    join(prerelease, "main.js"),
    "console.log('prerelease-version')",
  );
  writeFileSync(
    join(share, "2.0.0", "dist", "cli", "main.js"),
    "console.log('stable-version')",
  );
  writeFileSync(join(directory, ".thc", "version"), "1.0.0\n");
  const { execFileSync } = await import("node:child_process");
  const shim = join(home, ".local", "bin", "thc");
  const env = { ...process.env, HOME: home };
  expect(
    execFileSync(process.execPath, [shim, "status", "--dir", directory], {
      cwd: root,
      env,
      encoding: "utf8",
    }).trim(),
  ).toBe("folder-version");
  expect(
    execFileSync(process.execPath, [shim, "status"], {
      cwd: root,
      env,
      encoding: "utf8",
    }).trim(),
  ).toBe("stable-version");
});
