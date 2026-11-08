import type { Store, Turn } from "../store/index.js";

function relatedTurns(store: Store, turn: Turn): Turn[] {
  return store
    .listTurns([
      "active",
      "submitting",
      "uncertain",
      "completed",
      "failed",
      "cancelled",
      "interrupted",
    ])
    .filter((candidate) => {
      if (candidate.id === turn.id) return true;
      if (!turn.externalTurnId) return false;
      return (
        candidate.entityId === turn.entityId &&
        candidate.sessionId === turn.sessionId &&
        candidate.externalTurnId === turn.externalTurnId
      );
    });
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
