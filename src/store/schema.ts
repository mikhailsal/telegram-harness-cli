import type { DatabaseSync } from "node:sqlite";

const migrations = [
  `CREATE TABLE entities (id TEXT PRIMARY KEY, directory TEXT NOT NULL, botId TEXT NOT NULL, createdAt TEXT NOT NULL);
   CREATE TABLE harness_sessions (id TEXT PRIMARY KEY, entityId TEXT NOT NULL REFERENCES entities(id), harness TEXT NOT NULL, externalId TEXT NOT NULL, startedAt TEXT NOT NULL, endedAt TEXT);
   CREATE TABLE meta (name TEXT PRIMARY KEY, value TEXT NOT NULL);
   CREATE TABLE turns (id TEXT PRIMARY KEY, entityId TEXT NOT NULL REFERENCES entities(id), source TEXT NOT NULL, createdAt TEXT NOT NULL, status TEXT NOT NULL, sessionId TEXT REFERENCES harness_sessions(id), externalTurnId TEXT, updatedAt TEXT NOT NULL, result TEXT);
   CREATE TABLE inbox (updateId INTEGER PRIMARY KEY, entityId TEXT NOT NULL REFERENCES entities(id), chatId TEXT NOT NULL, userId TEXT NOT NULL, receivedAt TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued', turnId TEXT REFERENCES turns(id));
   CREATE TABLE pending_requests (id TEXT PRIMARY KEY, turnId TEXT NOT NULL REFERENCES turns(id), kind TEXT NOT NULL, payload TEXT NOT NULL, createdAt TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', decision TEXT);
   CREATE TABLE outbox (id TEXT PRIMARY KEY, turnId TEXT NOT NULL REFERENCES turns(id), chunk INTEGER NOT NULL, chatId TEXT NOT NULL, text TEXT NOT NULL, createdAt TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', messageId INTEGER, error TEXT, possiblyDuplicated INTEGER NOT NULL DEFAULT 0, retryUsed INTEGER NOT NULL DEFAULT 0, UNIQUE(turnId, chunk));
   CREATE TABLE messages (id TEXT PRIMARY KEY, chatId TEXT NOT NULL, telegramMessageId INTEGER NOT NULL, author TEXT NOT NULL, sentAt TEXT NOT NULL, text TEXT NOT NULL, turnId TEXT REFERENCES turns(id), possiblyDuplicated INTEGER NOT NULL DEFAULT 0, UNIQUE(chatId, telegramMessageId));
   CREATE VIRTUAL TABLE messages_fts USING fts5(text, content='messages', content_rowid='rowid');
   CREATE TRIGGER messages_insert AFTER INSERT ON messages BEGIN INSERT INTO messages_fts(rowid,text) VALUES(new.rowid,new.text); END;
   CREATE TRIGGER messages_delete AFTER DELETE ON messages BEGIN INSERT INTO messages_fts(messages_fts,rowid,text) VALUES('delete',old.rowid,old.text); END;
   CREATE TRIGGER messages_update AFTER UPDATE ON messages BEGIN INSERT INTO messages_fts(messages_fts,rowid,text) VALUES('delete',old.rowid,old.text); INSERT INTO messages_fts(rowid,text) VALUES(new.rowid,new.text); END;`,
  `CREATE TABLE transient (id TEXT PRIMARY KEY, kind TEXT NOT NULL, chatId TEXT NOT NULL, createdAt TEXT NOT NULL, expiresAt TEXT, messageId INTEGER, sentAt TEXT);
   CREATE TABLE attachments (updateId INTEGER NOT NULL REFERENCES inbox(updateId), fileUniqueId TEXT NOT NULL, fileKey TEXT NOT NULL, path TEXT NOT NULL, createdAt TEXT NOT NULL, turnId TEXT REFERENCES turns(id), PRIMARY KEY(updateId,fileUniqueId));`,
  `ALTER TABLE pending_requests ADD COLUMN expiresAt TEXT;
   ALTER TABLE outbox ADD COLUMN format TEXT NOT NULL DEFAULT 'rich';
   ALTER TABLE outbox ADD COLUMN fileName TEXT;
   ALTER TABLE outbox ADD COLUMN payload TEXT;
   ALTER TABLE outbox ADD COLUMN replyToMessageId INTEGER;
   ALTER TABLE outbox ADD COLUMN disableNotification INTEGER NOT NULL DEFAULT 0;
   ALTER TABLE outbox ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0;
   ALTER TABLE outbox ADD COLUMN nextAttemptAt TEXT;
   DROP TABLE messages_fts;
   CREATE VIRTUAL TABLE messages_fts USING fts5(text, content='messages', content_rowid='rowid', tokenize='trigram');
   INSERT INTO messages_fts(messages_fts) VALUES('rebuild');`,
];

export const SCHEMA_VERSION = migrations.length;

export function migrate(db: DatabaseSync, target = SCHEMA_VERSION): void {
  const row = db.prepare("PRAGMA user_version").get() as {
    user_version: number;
  };
  if (row.user_version > SCHEMA_VERSION)
    throw new Error(`Unsupported database version ${String(row.user_version)}`);
  if (
    !Number.isInteger(target) ||
    target < row.user_version ||
    target > SCHEMA_VERSION
  )
    throw new Error("Invalid migration target");
  for (let index = row.user_version; index < target; index++) {
    const migration = migrations[index];
    if (migration === undefined) throw new Error("Missing migration");
    db.exec("BEGIN IMMEDIATE");
    try {
      db.exec(migration);
      db.exec(`PRAGMA user_version = ${String(index + 1)}`);
      db.exec("COMMIT");
    } catch (error) {
      db.exec("ROLLBACK");
      throw error;
    }
  }
}
