import { QueueStore } from "./queue.js";
import type {
  PendingRequest,
  PendingRequestInput,
  RequestStatus,
} from "./types.js";

export class RequestStore extends QueueStore {
  createRequest(row: PendingRequestInput): void {
    this.db
      .prepare(
        "INSERT INTO pending_requests(id,turnId,kind,payload,createdAt,expiresAt) VALUES (?,?,?,?,?,?)",
      )
      .run(
        row.id,
        row.turnId,
        row.kind,
        row.payload,
        row.createdAt,
        row.expiresAt ?? null,
      );
  }
  getRequest(id: string): PendingRequest | undefined {
    return this.db
      .prepare("SELECT * FROM pending_requests WHERE id=?")
      .get(id) as PendingRequest | undefined;
  }
  listRequests(statuses: RequestStatus[]): PendingRequest[] {
    if (!statuses.length) return [];
    return this.db
      .prepare(
        `SELECT * FROM pending_requests WHERE status IN (${statuses.map(() => "?").join(",")}) ORDER BY createdAt,id`,
      )
      .all(...statuses) as unknown as PendingRequest[];
  }
  answerRequest(
    id: string,
    decision: string,
    decidedAt = new Date().toISOString(),
  ): boolean {
    return this.atomic(() => {
      const changed =
        this.db
          .prepare(
            "UPDATE pending_requests SET status='answering',decision=? WHERE id=? AND status='pending'",
          )
          .run(decision, id).changes === 1;
      if (changed) this.setCursor(`request-resolved-at/${id}`, decidedAt);
      return changed;
    });
  }
  resolveRequest(
    id: string,
    expected: "pending" | "answering",
    next: "answered" | "resolved_elsewhere" | "stale" | "expired" | "cancelled",
    resolvedAt = new Date().toISOString(),
  ): boolean {
    if (next === "answered" && expected !== "answering") return false;
    return this.atomic(() => {
      const changed =
        this.db
          .prepare(
            "UPDATE pending_requests SET status=? WHERE id=? AND status=?",
          )
          .run(next, id, expected).changes === 1;
      const key = `request-resolved-at/${id}`;
      if (changed && !this.getCursor(key)) this.setCursor(key, resolvedAt);
      return changed;
    });
  }
}
