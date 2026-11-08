import { OutboxStore } from "./outbox.js";
import type {
  Attachment,
  AttachmentInput,
  Transient,
  TransientInput,
} from "./types.js";

export class ResourceStore extends OutboxStore {
  putTransient(row: TransientInput): boolean {
    return (
      this.db
        .prepare(
          "INSERT OR IGNORE INTO transient(id,kind,chatId,createdAt,expiresAt) VALUES (?,?,?,?,?)",
        )
        .run(row.id, row.kind, row.chatId, row.createdAt, row.expiresAt)
        .changes === 1
    );
  }
  setTransientMessage(id: string, messageId: number, sentAt: string): boolean {
    return (
      this.db
        .prepare(
          "UPDATE transient SET messageId=?,sentAt=? WHERE id=? AND messageId IS NULL",
        )
        .run(messageId, sentAt, id).changes === 1
    );
  }
  replaceTransientMessage(
    id: string,
    previousMessageId: number,
    messageId: number,
    sentAt: string,
    expiresAt: string | null,
  ): boolean {
    return (
      this.db
        .prepare(
          "UPDATE transient SET messageId=?,sentAt=?,expiresAt=? WHERE id=? AND messageId=?",
        )
        .run(messageId, sentAt, expiresAt, id, previousMessageId).changes === 1
    );
  }
  listTransients(): Transient[] {
    return this.db
      .prepare("SELECT * FROM transient ORDER BY createdAt,id")
      .all() as unknown as Transient[];
  }
  deleteTransient(id: string): boolean {
    return (
      this.db.prepare("DELETE FROM transient WHERE id=?").run(id).changes === 1
    );
  }
  putAttachment(row: AttachmentInput): boolean {
    return (
      this.db
        .prepare("INSERT OR IGNORE INTO attachments VALUES (?,?,?,?,?,?)")
        .run(
          row.updateId,
          row.fileUniqueId,
          row.fileKey,
          row.path,
          row.createdAt,
          row.turnId,
        ).changes === 1
    );
  }
  getAttachment(
    updateId: number,
    fileUniqueId: string,
  ): Attachment | undefined {
    return this.db
      .prepare("SELECT * FROM attachments WHERE updateId=? AND fileUniqueId=?")
      .get(updateId, fileUniqueId) as Attachment | undefined;
  }
  assignAttachment(
    updateId: number,
    fileUniqueId: string,
    turnId: string,
  ): boolean {
    return (
      this.db
        .prepare(
          "UPDATE attachments SET turnId=? WHERE updateId=? AND fileUniqueId=? AND turnId IS NULL",
        )
        .run(turnId, updateId, fileUniqueId).changes === 1
    );
  }
  listAttachments(): Attachment[] {
    return this.db
      .prepare(
        "SELECT * FROM attachments ORDER BY createdAt,updateId,fileUniqueId",
      )
      .all() as unknown as Attachment[];
  }
  listRetainedAttachments(): Attachment[] {
    return this.db
      .prepare(
        "SELECT a.* FROM attachments a JOIN inbox i ON i.updateId=a.updateId LEFT JOIN turns t ON t.id=a.turnId WHERE i.status IN ('queued','assigned') OR t.status IN ('queued','submitting','active','uncertain') ORDER BY a.createdAt",
      )
      .all() as unknown as Attachment[];
  }
  removeUnretainedAttachment(
    updateId: number,
    fileUniqueId: string,
    expectedPath: string,
    remove: () => void,
  ): boolean {
    return this.atomic(() => {
      const row = this.getAttachment(updateId, fileUniqueId);
      if (
        row?.path !== expectedPath ||
        this.listRetainedAttachments().some(
          (item) =>
            item.updateId === updateId && item.fileUniqueId === fileUniqueId,
        )
      )
        return false;
      remove();
      return this.deleteAttachment(updateId, fileUniqueId);
    });
  }
  deleteAttachment(updateId: number, fileUniqueId: string): boolean {
    return (
      this.db
        .prepare(
          "DELETE FROM attachments WHERE updateId=? AND fileUniqueId=? AND NOT EXISTS (SELECT 1 FROM inbox i LEFT JOIN turns t ON t.id=attachments.turnId WHERE i.updateId=attachments.updateId AND (i.status IN ('queued','assigned') OR t.status IN ('queued','submitting','active','uncertain')))",
        )
        .run(updateId, fileUniqueId).changes === 1
    );
  }
}
