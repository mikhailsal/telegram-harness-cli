import { mkdtemp, mkdir, rm, symlink, writeFile } from "node:fs/promises";
import { DatabaseSync } from "node:sqlite";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { expect, it } from "vitest";
import { configuredDirectory } from "../../src/cli/init-options.js";
import { Store } from "../../src/store/index.js";
import { migrate } from "../../src/store/schema.js";

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

it("keeps a stored symlink identity when the store still has an older schema", async () => {
  const root = await mkdtemp(join(tmpdir(), "thc-legacy-schema-"));
  try {
    const directory = join(root, "project");
    const alias = join(root, "alias");
    await mkdir(join(directory, ".thc"), { recursive: true });
    await symlink(directory, alias);
    const database = join(directory, ".thc", "store.db");
    const db = new DatabaseSync(database);
    migrate(db, 1);
    db.prepare("INSERT INTO entities VALUES (?, ?, ?, ?)").run(
      "entity",
      alias,
      "123",
      "2026-01-01",
    );
    db.exec("INSERT INTO meta VALUES ('entity-id', 'entity')");
    db.close();
    expect(() => new Store(database, true)).toThrow(/requires migration/);
    expect(await configuredDirectory(directory)).toBe(alias);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

it("stops instead of guessing when an existing store cannot be read", async () => {
  const root = await mkdtemp(join(tmpdir(), "thc-legacy-unreadable-"));
  try {
    await mkdir(join(root, ".thc"));
    await writeFile(
      join(root, ".thc", "store.db"),
      "this is not a database file".repeat(16),
    );
    await expect(configuredDirectory(root)).rejects.toThrow();
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
