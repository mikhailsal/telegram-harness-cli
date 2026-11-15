import type { Store, Turn } from "../store/index.js";

function sameTurn(candidate: Turn, anchor: Turn): boolean {
  if (candidate.id === anchor.id) return true;
  if (!anchor.externalTurnId) return false;
  return (
    candidate.entityId === anchor.entityId &&
    candidate.sessionId === anchor.sessionId &&
    candidate.externalTurnId === anchor.externalTurnId
  );
}
// A turn's peers share its harness turn ID (accepted steers). A continuation
// also inherits the peers of the Telegram turn it continues.
function relatedTurns(store: Store, turn: Turn): Turn[] {
  const turns = store.listTurns([
    "active",
    "submitting",
    "uncertain",
    "completed",
    "failed",
    "cancelled",
    "interrupted",
  ]);
  const continued = store.getCursor(`continues/${turn.id}`);
  const root = turns.find((candidate) => candidate.id === continued);
  const anchors = root ? [turn, root] : [turn];
  return turns.filter((candidate) =>
    anchors.some((anchor) => sameTurn(candidate, anchor)),
  );
}
export function replySourceMessageId(
  store: Store,
  turn: Turn,
  chatId: string,
): number | undefined {
  const sources = relatedTurns(store, turn)
    .filter((candidate) => candidate.source === "telegram")
    .flatMap((candidate) => store.messagesForTurn(candidate.id))
    .filter(
      (message) =>
        message.chatId === chatId &&
        message.author === "user" &&
        message.id.startsWith("inbound/") &&
        message.telegramMessageId > 0,
    );
  const source = sources.sort(
    (a, b) => b.telegramMessageId - a.telegramMessageId,
  )[0];
  return source?.telegramMessageId;
}
export function replyTarget(
  store: Store,
  turn: Turn,
  chatId: string,
): number | undefined {
  const source = replySourceMessageId(store, turn, chatId);
  return source === store.latestVisibleMessageId(chatId) ? undefined : source;
}
