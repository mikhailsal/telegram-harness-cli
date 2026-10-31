import { stat } from "node:fs/promises";
import { randomUUID } from "node:crypto";
import { join } from "node:path";
import { backup, DatabaseSync } from "node:sqlite";
import { catalog, TelegramChannel } from "../channels/index.js";
import { SCHEMA_VERSION } from "../store/index.js";
import {
  ConfigurationError,
  noticeCredentials,
  readRawConfig,
  redact,
  resolveToken,
} from "./config.js";
import { DownNotice } from "./down-notice.js";
import { isMissing } from "./state.js";

export interface StartupOptions {
  apiRoot?: string;
  fetch?: typeof fetch;
}
export async function notifyStartupFailure(
  directory: string,
  error: unknown,
  options: StartupOptions = {},
): Promise<void> {
  let raw: unknown;
  try {
    raw = await readRawConfig(directory);
  } catch {
    return;
  }
  const credentials = noticeCredentials(raw);
  if (!credentials) return;
  const token = await resolveToken(credentials.token, directory);
  const channel = new TelegramChannel({
    token,
    allowedUserIds: credentials.allowlist,
    ...options,
  });
  const notice = new DownNotice(directory, {
    send: async (chatId, text) => {
      const id = (await channel.send(chatId, text)).messageIds[0];
      if (id === undefined)
        throw new Error("Startup notice returned no message id");
      return id;
    },
    delete: (chatId, messageId) => channel.delete(chatId, messageId),
  });
  const language = (await notice.language()) ?? "en";
  const path =
    error instanceof ConfigurationError
      ? (error.issues[0]?.path ?? "config")
      : "startup";
  const reason = redact(error instanceof Error ? error.message : String(error));
  await notice.show(
    `startup/${path}`,
    String(credentials.allowlist[0]),
    `${catalog[language].down}: ${reason}. thc doctor`,
  );
}

export async function backupBeforeMigration(directory: string): Promise<void> {
  const path = join(directory, ".thc", "store.db");
  try {
    await stat(path);
  } catch (error) {
    if (isMissing(error)) return;
    throw error;
  }
  const db = new DatabaseSync(path, { readOnly: true });
  try {
    const version = db.prepare("PRAGMA user_version").get()?.user_version;
    if (typeof version !== "number" || version >= SCHEMA_VERSION) return;
    const suffix = new Date().toISOString().replaceAll(":", "-");
    await backup(db, `${path}.bak-${suffix}-${randomUUID()}`);
  } finally {
    db.close();
  }
}
