import { MessageStore } from "./messages.js";
import { ResourceStore } from "./resources.js";

export type * from "./types.js";
export { SCHEMA_VERSION } from "./schema.js";
export class Store extends ResourceStore {}
export function openStore(path: string): Store {
  return new Store(path);
}

export type HistoryReader = Pick<
  MessageStore,
  | "searchMessages"
  | "historyStats"
  | "messagesAround"
  | "messagesAroundPage"
  | "messageCursor"
  | "messagesForDay"
  | "messagesBetween"
  | "messagesForTurn"
  | "close"
>;
export function openHistory(path: string): HistoryReader {
  return new MessageStore(path, true);
}
