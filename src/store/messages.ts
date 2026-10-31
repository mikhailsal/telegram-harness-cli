import { RequestStore } from "./requests.js";
import { searchQuery } from "./search.js";
import type {
  InboxInput,
  Inbox,
  MessagePage,
  SearchOptions,
  VisibleMessage,
  VisibleMessageInput,
} from "./types.js";

function pageLimit(limit: number): number {
  if (!Number.isInteger(limit) || limit < 1 || limit > 100)
    throw new RangeError("limit must be between 1 and 100");
  return limit;
}
function cursor(message: VisibleMessage): string {
  return Buffer.from(JSON.stringify([message.sentAt, message.id])).toString(
    "base64url",
  );
}
function decode(value: string | undefined): [string, string] {
  if (!value) return ["9999", "\uffff"];
  const parsed: unknown = JSON.parse(
    Buffer.from(value, "base64url").toString("utf8"),
  );
  if (
    !Array.isArray(parsed) ||
    parsed.length !== 2 ||
    typeof parsed[0] !== "string" ||
    typeof parsed[1] !== "string"
  )
    throw new Error("Invalid history cursor");
  return [parsed[0], parsed[1]];
}
type StoredMessage = Omit<VisibleMessage, "possiblyDuplicated"> & {
  possiblyDuplicated: number;
};
type InternalHistoryInput = Omit<VisibleMessageInput, "telegramMessageId">;
function normalize(row: StoredMessage): VisibleMessage {
  return { ...row, possiblyDuplicated: Boolean(row.possiblyDuplicated) };
}

export class MessageStore extends RequestStore {
  recordVisibleMessage(row: VisibleMessageInput): boolean {
    return (
      this.db
        .prepare("INSERT OR IGNORE INTO messages VALUES (?,?,?,?,?,?,?,?)")
        .run(
          row.id,
          row.chatId,
          row.telegramMessageId,
          row.author,
          row.sentAt,
          row.text,
          row.turnId,
          Number(row.possiblyDuplicated),
        ).changes === 1
    );
  }
  updateVisibleMessage(
    chatId: string,
    telegramMessageId: number,
    text: string,
  ): boolean {
    return (
      this.db
        .prepare(
          "UPDATE messages SET text=? WHERE chatId=? AND telegramMessageId=?",
        )
        .run(text, chatId, telegramMessageId).changes === 1
    );
  }
  recordIncoming(row: InboxInput, history: VisibleMessageInput): boolean {
    if (history.author !== "user" || history.chatId !== row.chatId)
      throw new Error("Incoming history does not match inbox");
    return this.atomic(() => {
      if (!this.recordInbox(row)) return false;
      if (!this.recordVisibleMessage(history))
        throw new Error("Incoming history already exists");
      return true;
    });
  }
  visibleMessage(
    chatId: string,
    telegramMessageId: number,
  ): VisibleMessage | undefined {
    const row = this.db
      .prepare("SELECT * FROM messages WHERE chatId=? AND telegramMessageId=?")
      .get(chatId, telegramMessageId) as StoredMessage | undefined;
    return row ? normalize(row) : undefined;
  }
  latestVisibleMessageId(chatId: string): number | undefined {
    const row = this.db
      .prepare(
        "SELECT max(telegramMessageId) AS id FROM messages WHERE chatId=? AND telegramMessageId>0",
      )
      .get(chatId) as { id: number | null };
    return row.id ?? undefined;
  }
  recordReactionControl(
    updateId: number,
    history?: InternalHistoryInput,
    context?: string,
  ): boolean {
    return this.atomic(() => {
      const inbox = this.getInbox(updateId);
      if (inbox?.status !== "queued") return false;
      if (history) this.recordReactionHistory(inbox, history, context);
      if (!this.consumeControl(updateId))
        throw new Error("Inbox changed during transaction");
      return true;
    });
  }
  recordDecisionHistory(history: InternalHistoryInput): boolean {
    if (
      !history.id.startsWith("request-summary/") ||
      history.author !== "system"
    )
      throw new Error("Invalid decision history");
    return this.atomic(() => {
      this.discardLegacyDecisionNotice(history.id);
      const inserted = this.insertInternalMessage(history);
      this.setCursor(
        `request-history/${history.id.slice("request-summary/".length)}`,
        "1",
      );
      return inserted;
    });
  }
  discardLegacyDecisionNotice(id: string): void {
    if (!id.startsWith("request-summary/"))
      throw new Error("Invalid decision notice");
    this.db
      .prepare(
        "DELETE FROM outbox WHERE id=? AND chunk<0 AND status IN ('pending','failed')",
      )
      .run(id);
  }
  private insertInternalMessage(history: InternalHistoryInput): boolean {
    const row = this.db
      .prepare(
        "SELECT min(telegramMessageId) AS id FROM messages WHERE telegramMessageId<0",
      )
      .get() as { id: number | null };
    return this.recordVisibleMessage({
      ...history,
      telegramMessageId: (row.id ?? 0) - 1,
    });
  }
  captureReaction(turnId: string): void {
    const key = `submitted-reaction/${turnId}`;
    if (this.getCursor(key)) return;
    const context = this.getCursor("next-reaction");
    if (context) this.setCursor(key, context);
  }
  acceptReaction(turnId: string): void {
    this.atomic(() => {
      const key = `submitted-reaction/${turnId}`;
      const captured = this.getCursor(key);
      if (!captured) return;
      if (this.getCursor("next-reaction") === captured)
        this.setCursor("next-reaction", "");
      this.setCursor(key, "");
    });
  }
  private recordReactionHistory(
    inbox: Inbox,
    history: InternalHistoryInput,
    context?: string,
  ): void {
    if (
      history.chatId !== inbox.chatId ||
      history.author !== "user" ||
      !context
    )
      throw new Error("Reaction history does not match inbox");
    if (!this.insertInternalMessage(history))
      throw new Error("Reaction history already exists");
    const lines = [
      ...(this.getCursor("next-reaction") ?? "").split("\n").filter(Boolean),
      context,
    ];
    while (lines.join("\n").length > 8192 && lines.length > 1) lines.shift();
    this.setCursor("next-reaction", lines.join("\n"));
  }
  linkMessageToTurn(id: string, turnId: string): boolean {
    return (
      this.db
        .prepare("UPDATE messages SET turnId=? WHERE id=? AND turnId IS NULL")
        .run(turnId, id).changes === 1
    );
  }
  messagesForTurn(turnId: string): VisibleMessage[] {
    const rows = this.db
      .prepare("SELECT * FROM messages WHERE turnId=? ORDER BY sentAt,id")
      .all(turnId) as unknown as StoredMessage[];
    return rows.map(normalize);
  }
  protected listPage(
    sql: string,
    values: string[],
    limit: number,
    before?: string,
  ): MessagePage {
    const count = pageLimit(limit);
    const [time, id] = decode(before);
    const rows = this.db
      .prepare(
        `${sql} AND (m.sentAt < ? OR (m.sentAt = ? AND m.id < ?)) ORDER BY m.sentAt DESC,m.id DESC LIMIT ?`,
      )
      .all(...values, time, time, id, count + 1) as unknown as StoredMessage[];
    const hasMore = rows.length > count;
    const messages = rows.slice(0, count).map(normalize);
    const last = messages.at(-1);
    return { messages, nextBefore: hasMore && last ? cursor(last) : null };
  }
  searchMessages(
    query: string,
    limit: number,
    before?: string,
    options: SearchOptions = {},
  ): MessagePage {
    const prepared = searchQuery(query, options);
    return this.listPage(prepared.sql, prepared.values, limit, before);
  }
  historyStats(): { count: number; first: string | null; last: string | null } {
    return this.db
      .prepare(
        "SELECT count(*) AS count,min(sentAt) AS first,max(sentAt) AS last FROM messages",
      )
      .get() as { count: number; first: string | null; last: string | null };
  }
  messagesAround(at: string, limit: number): VisibleMessage[] {
    const count = pageLimit(limit);
    let after = this.db
      .prepare(
        "SELECT * FROM messages WHERE sentAt > ? ORDER BY sentAt,id LIMIT ?",
      )
      .all(at, Math.floor(count / 2)) as unknown as StoredMessage[];
    const before = this.db
      .prepare(
        "SELECT * FROM messages WHERE sentAt <= ? ORDER BY sentAt DESC,id DESC LIMIT ?",
      )
      .all(at, count - after.length) as unknown as StoredMessage[];
    if (before.length + after.length < count)
      after = this.db
        .prepare(
          "SELECT * FROM messages WHERE sentAt > ? ORDER BY sentAt,id LIMIT ?",
        )
        .all(at, count - before.length) as unknown as StoredMessage[];
    return [...before.reverse(), ...after].map(normalize);
  }
  messagesAroundPage(at: string, limit: number, before?: string): MessagePage {
    if (before)
      return this.listPage(
        "SELECT m.* FROM messages m WHERE 1=1",
        [],
        limit,
        before,
      );
    const messages = this.messagesAround(at, limit).reverse();
    const last = messages.at(-1);
    if (!last) return { messages, nextBefore: null };
    const next = cursor(last);
    return {
      messages,
      nextBefore: this.listPage(
        "SELECT m.* FROM messages m WHERE 1=1",
        [],
        1,
        next,
      ).messages.length
        ? next
        : null,
    };
  }
  messageCursor(message: VisibleMessage): string {
    return cursor(message);
  }
  messagesBetween(
    start: string,
    end: string,
    limit: number,
    before?: string,
  ): MessagePage {
    if (start >= end) throw new Error("Invalid time range");
    return this.listPage(
      "SELECT m.* FROM messages m WHERE m.sentAt>=? AND m.sentAt<?",
      [start, end],
      limit,
      before,
    );
  }
  messagesForDay(day: string, limit: number, before?: string): MessagePage {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) throw new Error("Invalid day");
    return this.listPage(
      "SELECT m.* FROM messages m WHERE substr(m.sentAt,1,10)=?",
      [day],
      limit,
      before,
    );
  }
}
