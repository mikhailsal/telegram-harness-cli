import { MessageStore } from "./messages.js";
import type {
  Outbox,
  OutboxInput,
  OutboxStatus,
  TurnPatch,
  TurnStatus,
  VisibleMessageInput,
} from "./types.js";

type StoredOutbox = Omit<
  Outbox,
  "possiblyDuplicated" | "retryUsed" | "disableNotification"
> & {
  possiblyDuplicated: number;
  retryUsed: number;
  disableNotification: number;
};
function normalize(row: StoredOutbox | undefined): Outbox | undefined {
  return (
    row && {
      ...row,
      possiblyDuplicated: Boolean(row.possiblyDuplicated),
      retryUsed: Boolean(row.retryUsed),
      disableNotification: Boolean(row.disableNotification),
    }
  );
}
export class OutboxStore extends MessageStore {
  enqueueOutbox(row: OutboxInput): boolean {
    return (
      this.db
        .prepare(
          "INSERT OR IGNORE INTO outbox(id,turnId,chunk,chatId,text,createdAt,format,fileName,payload,replyToMessageId,disableNotification) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        )
        .run(
          row.id,
          row.turnId,
          row.chunk,
          row.chatId,
          row.text,
          row.createdAt,
          row.format ?? "rich",
          row.fileName ?? null,
          row.payload ?? null,
          row.replyToMessageId ?? null,
          Number(row.disableNotification ?? false),
        ).changes === 1
    );
  }
  finishTurnAndEnqueue(
    id: string,
    expected: TurnStatus,
    next: "completed" | "failed" | "cancelled" | "interrupted",
    patch: TurnPatch,
    rows: OutboxInput[],
  ): boolean {
    if (rows.some((row) => row.turnId !== id))
      throw new Error("Outbox belongs to another turn");
    return this.atomic(() => {
      if (!this.transitionTurn(id, expected, next, patch)) return false;
      for (const row of rows) {
        if (!this.enqueueOutbox(row)) throw new Error("Duplicate outbox chunk");
      }
      for (const inbox of this.listInbox(["assigned"])) {
        if (inbox.turnId === id) this.consumeInbox(inbox.updateId);
      }
      return true;
    });
  }
  getOutbox(id: string): Outbox | undefined {
    return normalize(
      this.db.prepare("SELECT * FROM outbox WHERE id=?").get(id) as unknown as
        StoredOutbox | undefined,
    );
  }
  listOutbox(statuses: OutboxStatus[]): Outbox[] {
    if (!statuses.length) return [];
    const rows = this.db
      .prepare(
        `SELECT * FROM outbox WHERE status IN (${statuses.map(() => "?").join(",")}) ORDER BY createdAt,id`,
      )
      .all(...statuses) as unknown as StoredOutbox[];
    return rows.map((row) => ({
      ...row,
      possiblyDuplicated: Boolean(row.possiblyDuplicated),
      retryUsed: Boolean(row.retryUsed),
      disableNotification: Boolean(row.disableNotification),
    }));
  }
  beginSend(id: string, now = new Date().toISOString()): boolean {
    return (
      this.db
        .prepare(
          "UPDATE outbox SET status='sending',attempts=attempts+1,nextAttemptAt=NULL WHERE id=? AND status='pending' AND (nextAttemptAt IS NULL OR nextAttemptAt<=?)",
        )
        .run(id, now).changes === 1
    );
  }
  markSent(
    id: string,
    telegramMessageId: number,
    history: VisibleMessageInput,
  ): boolean {
    return this.atomic(() => {
      const row = this.getOutbox(id);
      if (row?.status !== "sending") return false;
      if (row.messageId !== null && row.messageId !== telegramMessageId)
        throw new Error("Delivered message differs from its checkpoint");
      if (
        history.telegramMessageId !== telegramMessageId ||
        history.chatId !== row.chatId ||
        history.turnId !== row.turnId
      )
        throw new Error("Delivered history does not match outbox");
      if (
        !this.recordVisibleMessage({
          ...history,
          possiblyDuplicated: row.possiblyDuplicated,
        })
      )
        throw new Error("Delivered history already exists");
      return (
        this.db
          .prepare(
            "UPDATE outbox SET status='sent',messageId=?,error=NULL WHERE id=? AND status='sending'",
          )
          .run(telegramMessageId, id).changes === 1
      );
    });
  }
  scheduleRetry(id: string, error: string, nextAttemptAt: string): boolean {
    return (
      this.db
        .prepare(
          "UPDATE outbox SET status='pending',error=?,nextAttemptAt=? WHERE id=? AND status='sending'",
        )
        .run(error, nextAttemptAt, id).changes === 1
    );
  }
  checkpointMessage(id: string, messageId: number): boolean {
    return (
      this.db
        .prepare(
          "UPDATE outbox SET messageId=? WHERE id=? AND status='sending' AND (messageId IS NULL OR messageId=?)",
        )
        .run(messageId, id, messageId).changes === 1
    );
  }
  setDeliveryFormat(
    id: string,
    format: "rich" | "plain" | "document",
    fileName: string | null,
    payload: string | null,
  ): boolean {
    return (
      this.db
        .prepare(
          "UPDATE outbox SET format=?,fileName=?,payload=? WHERE id=? AND status='sending'",
        )
        .run(format, fileName, payload, id).changes === 1
    );
  }
  markFailed(id: string, error: string): boolean {
    return (
      this.db
        .prepare(
          "UPDATE outbox SET status='failed',error=? WHERE id=? AND status='sending'",
        )
        .run(error, id).changes === 1
    );
  }
  recoverSending(): number {
    return Number(
      this.db
        .prepare(
          "UPDATE outbox SET status='pending',possiblyDuplicated=CASE WHEN messageId IS NULL THEN 1 ELSE possiblyDuplicated END WHERE status='sending'",
        )
        .run().changes,
    );
  }
  retryFailed(): number {
    return Number(
      this.db
        .prepare(
          "UPDATE outbox SET status='pending',retryUsed=1 WHERE status='failed' AND retryUsed=0",
        )
        .run().changes,
    );
  }
}
