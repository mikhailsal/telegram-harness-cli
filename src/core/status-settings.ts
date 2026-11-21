import type { catalog } from "../channels/index.js";
import type { Store } from "../store/index.js";

// A turn's submit execution record; {} when it is missing or damaged.
function executionRecord(
  store: Store,
  turnId: string,
): Record<string, unknown> {
  const raw = store.getCursor(`turn-execution/${turnId}/submit`);
  try {
    const record: unknown = raw ? JSON.parse(raw) : undefined;
    return record && typeof record === "object"
      ? (record as Record<string, unknown>)
      : {};
  } catch {
    return {};
  }
}

// The settings Telegram sent with a turn, from its execution record, for the
// live status: "" when it sent none, as the turn then runs on Codex's own.
export function sentSettings(
  store: Store,
  turnId: string,
  strings: (typeof catalog)["en"],
): string {
  const record = executionRecord(store, turnId);
  const field = (name: string) => {
    const value = record[name];
    return typeof value === "string" && value !== "none" ? value : undefined;
  };
  const effort = field("effort");
  const tier = field("serviceTier");
  return [
    field("model"),
    effort && `${strings.effortShort} ${effort}`,
    tier && tier !== "standard" && strings.fastShort,
  ]
    .filter(Boolean)
    .join(" · ");
}
