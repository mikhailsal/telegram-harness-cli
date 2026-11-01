import { createHash } from "node:crypto";
import type { Turn } from "../store/index.js";

export function turnDraftId(turn: Pick<Turn, "id" | "createdAt">): number {
  const hash = createHash("sha256").update(turn.id).digest("hex");
  return Number.parseInt(hash.slice(0, 13), 16) || 1;
}
