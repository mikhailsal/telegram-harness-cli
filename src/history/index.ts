import type {
  HistoryReader,
  MessagePage,
  SearchOptions,
  VisibleMessage,
} from "../store/index.js";

export interface HistoryEntry {
  author: VisibleMessage["author"];
  sentAt: string;
  text: string;
  possiblyDuplicated: boolean;
  remainingCharacters: number;
}
export interface HistoryPage {
  entries: HistoryEntry[];
  nextBefore: string | null;
}
export interface HistoryPageOptions extends SearchOptions {
  limit?: number;
  before?: string;
  maxChars?: number;
}
function budget(value: number | undefined): number {
  const count = value ?? 8000;
  if (!Number.isInteger(count) || count < 1 || count > 100000)
    throw new RangeError("maxChars must be between 1 and 100000");
  return count;
}
function entry(message: VisibleMessage, maxChars: number): HistoryEntry {
  const chars = Array.from(message.text);
  return {
    author: message.author,
    sentAt: message.sentAt,
    text: chars.slice(0, maxChars).join(""),
    possiblyDuplicated: message.possiblyDuplicated,
    remainingCharacters: Math.max(0, chars.length - maxChars),
  };
}
function page(
  fetch: (before: string | undefined) => MessagePage,
  options: HistoryPageOptions,
): HistoryPage {
  const limit = options.limit ?? 25;
  if (!Number.isInteger(limit) || limit < 1 || limit > 100)
    throw new RangeError("limit must be between 1 and 100");
  const maximum = budget(options.maxChars);
  const entries: HistoryEntry[] = [];
  let position = options.before;
  let remaining = maximum;
  let nextBefore: string | null = null;
  while (entries.length < limit && remaining > 0) {
    const result = fetch(position);
    const message = result.messages[0];
    if (!message) break;
    const item = entry(message, remaining);
    entries.push(item);
    remaining -= Array.from(item.text).length;
    nextBefore = result.nextBefore;
    if (!nextBefore) break;
    position = nextBefore;
  }
  return { entries, nextBefore };
}
export function search(
  store: HistoryReader,
  query: string,
  options: HistoryPageOptions = {},
): HistoryPage {
  return page(
    (before) => store.searchMessages(query, 1, before, options),
    options,
  );
}
export function around(
  store: HistoryReader,
  at: string,
  limit = 25,
  maxChars = 8000,
): HistoryEntry[] {
  let remaining = budget(maxChars);
  const entries: HistoryEntry[] = [];
  for (const message of store.messagesAround(at, limit)) {
    if (remaining <= 0) break;
    const item = entry(message, remaining);
    entries.push(item);
    remaining -= Array.from(item.text).length;
  }
  return entries;
}
export function day(
  store: HistoryReader,
  date: string,
  options: HistoryPageOptions = {},
): HistoryPage {
  return page((before) => store.messagesForDay(date, 1, before), options);
}
