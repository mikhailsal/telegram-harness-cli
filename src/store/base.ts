import { DatabaseSync } from "node:sqlite";
import { migrate, SCHEMA_VERSION } from "./schema.js";

export class StoreBase {
  protected readonly db: DatabaseSync;

  constructor(path: string, readOnly = false) {
    this.db = new DatabaseSync(path, { readOnly });
    try {
      this.db.exec("PRAGMA foreign_keys = ON; PRAGMA busy_timeout = 5000");
      if (readOnly) {
        const version = this.db.prepare("PRAGMA user_version").get() as {
          user_version: number;
        };
        if (version.user_version !== SCHEMA_VERSION)
          throw new Error("Database requires migration");
      } else {
        this.db.exec("PRAGMA journal_mode = WAL");
        migrate(this.db);
      }
    } catch (error) {
      this.db.close();
      throw error;
    }
  }

  close(): void {
    this.db.close();
  }

  protected atomic<T>(action: () => T): T {
    this.db.exec("BEGIN IMMEDIATE");
    try {
      const value = action();
      this.db.exec("COMMIT");
      return value;
    } catch (error) {
      this.db.exec("ROLLBACK");
      throw error;
    }
  }
}
