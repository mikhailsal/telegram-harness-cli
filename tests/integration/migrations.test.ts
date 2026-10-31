import { spawnSync } from "node:child_process";
import { mkdtempSync, mkdirSync, readdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { DatabaseSync } from "node:sqlite";
import { afterEach, expect, it } from "vitest";
import { migrate, SCHEMA_VERSION } from "../../src/store/schema.js";

const resources: { directory: string; db: DatabaseSync }[] = [];
afterEach(() => {
  for (const { directory, db } of resources.splice(0)) {
    db.close();
    rmSync(directory, { recursive: true, force: true });
  }
});
function legacy(version: number) {
  const directory = mkdtempSync(join(tmpdir(), "thc-upgrade-"));
  mkdirSync(join(directory, ".thc"));
  const path = join(directory, ".thc", "store.db");
  const db = new DatabaseSync(path);
  db.exec("PRAGMA journal_mode=WAL");
  migrate(db, version);
  if (version)
    db.prepare("INSERT INTO messages VALUES(?,?,?,?,?,?,?,?)").run(
      "visible",
      "1",
      1,
      "user",
      "2026-10-31T12:00:00Z",
      "visible migration kiwi",
      null,
      0,
    );
  resources.push({ directory, db });
  return { directory, path, db };
}
function upgrade(directory: string) {
  const script = `
    const root = process.argv[2];
    const { backupBeforeMigration } = await import(root + '/core/startup.js');
    const { openStore } = await import(root + '/store/index.js');
    await backupBeforeMigration(process.argv[1]);
    const store = openStore(process.argv[1] + '/.thc/store.db');
    console.log(JSON.stringify(store.searchMessages('kiwi', 10)));
    store.close();
  `;
  return spawnSync(
    process.execPath,
    [
      "--input-type=module",
      "-e",
      script,
      directory,
      pathToFileURL(resolve("dist")).href,
    ],
    { encoding: "utf8", timeout: 10_000 },
  );
}
it("a separate process upgrades each supported database and preserves visible history with a consistent backup", () => {
  for (const version of [1, 2]) {
    const fixture = legacy(version);
    const result = upgrade(fixture.directory);
    expect(result.status, result.stderr).toBe(0);
    expect(result.stdout).toContain("visible migration kiwi");
    const backups = readdirSync(join(fixture.directory, ".thc")).filter(
      (name) => name.startsWith("store.db.bak-"),
    );
    expect(backups).toHaveLength(1);
    const snapshot = new DatabaseSync(
      join(fixture.directory, ".thc", backups[0] ?? ""),
      { readOnly: true },
    );
    try {
      expect(snapshot.prepare("PRAGMA user_version").get()?.user_version).toBe(
        version,
      );
      expect(snapshot.prepare("SELECT text FROM messages").get()?.text).toBe(
        "visible migration kiwi",
      );
    } finally {
      snapshot.close();
    }
  }
});
it("a fresh database is initialized by a separate process and rejects a future schema without modifying it", () => {
  const fixture = legacy(0);
  expect(upgrade(fixture.directory).status).toBe(0);
  expect(fixture.db.prepare("PRAGMA user_version").get()?.user_version).toBe(
    SCHEMA_VERSION,
  );
  fixture.db.exec("PRAGMA user_version=999");
  const failed = upgrade(fixture.directory);
  expect(failed.status).not.toBe(0);
  expect(fixture.db.prepare("PRAGMA user_version").get()?.user_version).toBe(
    999,
  );
});
