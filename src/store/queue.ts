import { StoreBase } from "./base.js";
import type {
  Entity,
  HarnessSession,
  Inbox,
  InboxInput,
  Turn,
  TurnInput,
  TurnPatch,
  TurnStatus,
} from "./types.js";

const transitions: Record<TurnStatus, readonly TurnStatus[]> = {
  queued: ["submitting", "active", "cancelled", "failed"],
  submitting: [
    "active",
    "uncertain",
    "completed",
    "failed",
    "cancelled",
    "interrupted",
  ],
  active: ["completed", "failed", "cancelled", "interrupted"],
  uncertain: ["active", "completed", "failed", "cancelled", "interrupted"],
  completed: [],
  failed: [],
  cancelled: [],
  interrupted: [],
};

export class QueueStore extends StoreBase {
  putEntity(row: Entity): void {
    this.db
      .prepare(
        "INSERT INTO entities VALUES (?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET directory=excluded.directory, botId=excluded.botId",
      )
      .run(row.id, row.directory, row.botId, row.createdAt);
  }
  getEntity(id: string): Entity | undefined {
    return this.db.prepare("SELECT * FROM entities WHERE id=?").get(id) as
      Entity | undefined;
  }
  putSession(row: HarnessSession): void {
    this.db
      .prepare(
        "INSERT INTO harness_sessions VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET externalId=excluded.externalId, endedAt=excluded.endedAt",
      )
      .run(
        row.id,
        row.entityId,
        row.harness,
        row.externalId,
        row.startedAt,
        row.endedAt,
      );
  }
  activateSession(
    session: HarnessSession,
    descriptor: string,
    previousSessionId?: string,
    cursor = "",
  ): boolean {
    return this.atomic(() => {
      const active = this.getCursor("active-session");
      if ((active === "" ? undefined : active) !== previousSessionId)
        return false;
      if (
        this.listTurns(["submitting", "active", "uncertain"]).some(
          (turn) =>
            previousSessionId === undefined ||
            turn.sessionId === previousSessionId ||
            turn.sessionId === null,
        )
      )
        return false;
      this.putSession(session);
      if (
        previousSessionId &&
        !this.closeSession(previousSessionId, session.startedAt)
      )
        throw new Error("Previous session is missing or closed");
      this.setCursor("adapter-descriptor", descriptor);
      this.setCursor("active-session", session.id);
      this.setCursor("adapter-cursor", cursor);
      return true;
    });
  }
  getSession(id: string): HarnessSession | undefined {
    return this.db
      .prepare("SELECT * FROM harness_sessions WHERE id=?")
      .get(id) as HarnessSession | undefined;
  }
  listSessions(entityId: string): HarnessSession[] {
    return this.db
      .prepare(
        "SELECT * FROM harness_sessions WHERE entityId=? ORDER BY startedAt,id",
      )
      .all(entityId) as unknown as HarnessSession[];
  }
  closeSession(id: string, endedAt: string): boolean {
    return (
      this.db
        .prepare(
          "UPDATE harness_sessions SET endedAt=? WHERE id=? AND endedAt IS NULL",
        )
        .run(endedAt, id).changes === 1
    );
  }
  recordInbox(row: InboxInput): boolean {
    return (
      this.db
        .prepare(
          "INSERT OR IGNORE INTO inbox(updateId,entityId,chatId,userId,receivedAt,payload) VALUES (?,?,?,?,?,?)",
        )
        .run(
          row.updateId,
          row.entityId,
          row.chatId,
          row.userId,
          row.receivedAt,
          row.payload,
        ).changes === 1
    );
  }
  getInbox(updateId: number): Inbox | undefined {
    return this.db
      .prepare("SELECT * FROM inbox WHERE updateId=?")
      .get(updateId) as Inbox | undefined;
  }
  listQueuedInbox(): Inbox[] {
    return this.db
      .prepare("SELECT * FROM inbox WHERE status='queued' ORDER BY updateId")
      .all() as unknown as Inbox[];
  }
  listInbox(statuses: Inbox["status"][]): Inbox[] {
    if (!statuses.length) return [];
    return this.db
      .prepare(
        `SELECT * FROM inbox WHERE status IN (${statuses.map(() => "?").join(",")}) ORDER BY updateId`,
      )
      .all(...statuses) as unknown as Inbox[];
  }
  assignInbox(updateId: number, turnId: string): boolean {
    return (
      this.db
        .prepare(
          "UPDATE inbox SET status='assigned',turnId=? WHERE updateId=? AND status='queued'",
        )
        .run(turnId, updateId).changes === 1
    );
  }
  consumeInbox(updateId: number): boolean {
    return (
      this.db
        .prepare(
          "UPDATE inbox SET status='consumed' WHERE updateId=? AND status='assigned'",
        )
        .run(updateId).changes === 1
    );
  }
  setCursor(name: string, value: string): void {
    this.db
      .prepare(
        "INSERT INTO meta VALUES (?,?) ON CONFLICT(name) DO UPDATE SET value=excluded.value",
      )
      .run(name, value);
  }
  getCursor(name: string): string | undefined {
    return (
      this.db.prepare("SELECT value FROM meta WHERE name=?").get(name) as
        { value: string } | undefined
    )?.value;
  }
  createTurn(row: TurnInput): void {
    this.db
      .prepare(
        "INSERT INTO turns(id,entityId,source,createdAt,status,updatedAt) VALUES (?,?,?,?,'queued',?)",
      )
      .run(row.id, row.entityId, row.source, row.createdAt, row.createdAt);
  }
  queueInbox(
    row: TurnInput,
    updateIds: number[],
    visibleIds: readonly string[] = [],
  ): boolean {
    return this.atomic(() => {
      if (
        !updateIds.length ||
        updateIds.some((id) => {
          const inbox = this.getInbox(id);
          return inbox?.status !== "queued" || inbox.entityId !== row.entityId;
        })
      )
        return false;
      this.createTurn(row);
      for (const id of updateIds) {
        if (!this.assignInbox(id, row.id))
          throw new Error("Inbox assignment changed during transaction");
      }
      for (const id of visibleIds)
        this.db
          .prepare("UPDATE messages SET turnId=? WHERE id=?")
          .run(row.id, id);
      return true;
    });
  }
  consumeControl(updateId: number): boolean {
    return (
      this.db
        .prepare(
          "UPDATE inbox SET status='consumed' WHERE updateId=? AND status='queued'",
        )
        .run(updateId).changes === 1
    );
  }
  commitQuestionAnswer(
    updateId: number,
    requestId: string,
    progress: string,
    finalDecision?: string,
    decidedAt = new Date().toISOString(),
  ): boolean {
    return this.atomic(() => {
      if (this.getInbox(updateId)?.status !== "queued") return false;
      const request = this.db
        .prepare("SELECT status FROM pending_requests WHERE id=?")
        .get(requestId) as { status: string } | undefined;
      if (request?.status !== "pending") return false;
      this.setCursor(`question/${requestId}`, progress);
      if (
        finalDecision !== undefined &&
        this.db
          .prepare(
            "UPDATE pending_requests SET status='answering',decision=? WHERE id=? AND status='pending'",
          )
          .run(finalDecision, requestId).changes !== 1
      )
        throw new Error("Request changed during transaction");
      if (finalDecision !== undefined)
        this.setCursor(`request-resolved-at/${requestId}`, decidedAt);
      if (!this.consumeControl(updateId))
        throw new Error("Inbox changed during transaction");
      return true;
    });
  }
  getTurn(id: string): Turn | undefined {
    return this.db.prepare("SELECT * FROM turns WHERE id=?").get(id) as
      Turn | undefined;
  }
  listTurns(statuses: TurnStatus[]): Turn[] {
    if (!statuses.length) return [];
    return this.db
      .prepare(
        `SELECT * FROM turns WHERE status IN (${statuses.map(() => "?").join(",")}) ORDER BY createdAt,id`,
      )
      .all(...statuses) as unknown as Turn[];
  }
  requeueRejectedSteer(id: string, updatedAt: string): boolean {
    return (
      this.db
        .prepare(
          "UPDATE turns SET status='queued',sessionId=NULL,externalTurnId=NULL,updatedAt=? WHERE id=? AND status='submitting'",
        )
        .run(updatedAt, id).changes === 1
    );
  }
  transitionTurn(
    id: string,
    expected: TurnStatus,
    next: TurnStatus,
    patch: TurnPatch = {},
  ): boolean {
    if (!transitions[expected].includes(next)) return false;
    const current = this.getTurn(id);
    if (current?.status !== expected) return false;
    return (
      this.db
        .prepare(
          "UPDATE turns SET status=?,sessionId=?,externalTurnId=?,updatedAt=?,result=? WHERE id=? AND status=?",
        )
        .run(
          next,
          patch.sessionId === undefined ? current.sessionId : patch.sessionId,
          patch.externalTurnId === undefined
            ? current.externalTurnId
            : patch.externalTurnId,
          patch.updatedAt ?? current.updatedAt,
          patch.result === undefined ? current.result : patch.result,
          id,
          expected,
        ).changes === 1
    );
  }
}
