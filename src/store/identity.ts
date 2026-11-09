import { DatabaseSync } from "node:sqlite";
import type { Entity } from "./types.js";

export type StoreIdentity = Pick<Entity, "directory" | "botId">;

/** The tables that identify a project exist since the first schema. */
function hasIdentityTables(db: DatabaseSync): boolean {
  const found = db
    .prepare(
      "SELECT count(*) AS tables FROM sqlite_master WHERE type = 'table' AND name IN ('meta', 'entities')",
    )
    .get() as { tables: number };
  return found.tables === 2;
}

/**
 * The folder and bot a bridge persisted for its project. Only tables that
 * every schema version has are read, so a store that still has an older schema
 * is identified without being opened as a current store or migrated.
 */
export function readStoreIdentity(path: string): StoreIdentity | undefined {
  const db = new DatabaseSync(path, { readOnly: true });
  try {
    db.exec("PRAGMA busy_timeout = 5000");
    if (!hasIdentityTables(db)) return undefined;
    const row = db
      .prepare(
        "SELECT entities.directory AS directory, entities.botId AS botId FROM meta JOIN entities ON entities.id = meta.value WHERE meta.name = 'entity-id'",
      )
      .get() as StoreIdentity | undefined;
    return row && { directory: row.directory, botId: row.botId };
  } finally {
    db.close();
  }
}
