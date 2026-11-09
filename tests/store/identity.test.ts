import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { DatabaseSync } from "node:sqlite";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, expect, it } from "vitest";
import { Store } from "../../src/store/index.js";
import { readStoreIdentity } from "../../src/store/identity.js";
import { migrate } from "../../src/store/schema.js";

let root: string;
let path: string;
beforeEach(async () => {
  root = await mkdtemp(join(tmpdir(), "thc-identity-"));
  path = join(root, "store.db");
});
afterEach(async () => {
  await rm(root, { recursive: true, force: true });
});

it("reads the identity a bridge persisted", () => {
  const store = new Store(path);
  store.putEntity({
    id: "entity",
    directory: "/work/project",
    botId: "12345",
    createdAt: "2026-01-01",
  });
  store.setCursor("entity-id", "entity");
  store.close();
  expect(readStoreIdentity(path)).toEqual({
    directory: "/work/project",
    botId: "12345",
  });
});

it("reads the identity from a store kept at the first schema without migrating it", () => {
  const db = new DatabaseSync(path);
  migrate(db, 1);
  db.exec(
    "INSERT INTO entities VALUES ('entity', '/links/project', '777', '2026-01-01'); INSERT INTO meta VALUES ('entity-id', 'entity')",
  );
  db.close();
  expect(() => new Store(path, true)).toThrow(/requires migration/);
  expect(readStoreIdentity(path)).toEqual({
    directory: "/links/project",
    botId: "777",
  });
  const after = new DatabaseSync(path);
  const version = after.prepare("PRAGMA user_version").get() as {
    user_version: number;
  };
  after.close();
  expect(version.user_version).toBe(1);
});

it("has no identity before a bridge persisted one", () => {
  const store = new Store(path);
  expect(readStoreIdentity(path)).toBeUndefined();
  store.setCursor("entity-id", "never-stored");
  store.close();
  expect(readStoreIdentity(path)).toBeUndefined();
});

it("has no identity in a database that was never given a schema", () => {
  new DatabaseSync(path).close();
  expect(readStoreIdentity(path)).toBeUndefined();
});

it("reports a file that is not a database instead of guessing", async () => {
  await writeFile(
    path,
    "this is not a database file, only some text".repeat(8),
  );
  expect(() => readStoreIdentity(path)).toThrow();
});
