import { appendFile, mkdir, readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import type { Config } from "../core/config.js";
import {
  around,
  search,
  type HistoryEntry,
  type HistoryPageOptions,
} from "../history/index.js";
import { openHistory, type HistoryReader } from "../store/index.js";
import type { CliDependencies } from "./context.js";
import type { Arguments } from "./arguments.js";
import { allowedFlags, flag, positive } from "./arguments.js";
import { decodeCursor, encodeCursor } from "./cursor.js";
import { CliError, usage } from "./errors.js";
import { formatTime, parseTime } from "./time.js";

export function formatEntry(entry: HistoryEntry, timezone: string): string {
  const time = formatTime(entry.sentAt, timezone);
  const more = entry.remainingCharacters
    ? ` … ${String(entry.remainingCharacters)} more characters: thc around "${time}" --full`
    : "";
  return `[${time} · ${entry.author}] ${entry.text}${more}`;
}
async function usageLog(
  directory: string,
  command: string,
  words: number,
  hits: number,
  error: string | null,
): Promise<void> {
  const path = join(directory, ".thc", "usage.jsonl");
  try {
    await mkdir(join(directory, ".thc"), { recursive: true, mode: 0o700 });
    let lines: string[] = [];
    try {
      lines = (await readFile(path, "utf8")).split("\n").filter(Boolean);
    } catch {
      /* A missing or unreadable usage log must not block search. */
    }
    const cutoff = Date.now() - 30 * 86400000;
    lines = lines.filter((line) => {
      try {
        const row: unknown = JSON.parse(line);
        return (
          typeof row === "object" &&
          row !== null &&
          "time" in row &&
          typeof row.time === "string" &&
          Date.parse(row.time) >= cutoff
        );
      } catch {
        return false;
      }
    });
    await writeFile(path, lines.length ? `${lines.join("\n")}\n` : "", {
      mode: 0o600,
    });
    await appendFile(
      path,
      `${JSON.stringify({ time: new Date().toISOString(), command, words, hits, error })}\n`,
      { mode: 0o600 },
    );
  } catch {
    /* Read-only sandboxes may not be able to record usage. */
  }
}
function options(
  args: Arguments,
  timezone: string,
  directory: string,
): HistoryPageOptions {
  const role = flag(args, "role");
  if (role && role !== "user" && role !== "agent")
    throw usage("--role must be user or agent");
  const since = flag(args, "since");
  const result: HistoryPageOptions = {
    limit: positive(flag(args, "limit"), 25, 100),
    maxChars: positive(
      flag(args, "max-chars"),
      args.flags.has("full") ? 100000 : 8000,
      100000,
    ),
    any: args.flags.has("any"),
    raw: args.flags.has("fts"),
  };
  const before = flag(args, "after");
  if (before) result.before = decodeCursor(before, directory);
  if (role) result.role = role === "agent" ? "assistant" : "user";
  if (since) result.since = parseTime(since, timezone);
  return result;
}
function dayPage(
  reader: HistoryReader,
  start: string,
  end: string,
  settings: HistoryPageOptions,
): { entries: HistoryEntry[]; nextBefore: string | null } {
  const entries: HistoryEntry[] = [];
  let remaining = settings.maxChars ?? 8000;
  let before = settings.before;
  let nextBefore: string | null = null;
  while (entries.length < (settings.limit ?? 25) && remaining > 0) {
    const result = reader.messagesBetween(start, end, 1, before);
    const message = result.messages[0];
    if (!message) break;
    const chars = Array.from(message.text);
    const text = chars.slice(0, remaining).join("");
    remaining -= Array.from(text).length;
    entries.push({
      author: message.author,
      sentAt: message.sentAt,
      text,
      possiblyDuplicated: message.possiblyDuplicated,
      remainingCharacters: chars.length - Array.from(text).length,
    });
    nextBefore = result.nextBefore;
    if (!nextBefore) break;
    before = nextBefore;
  }
  return { entries, nextBefore };
}
function findDay(
  reader: HistoryReader,
  query: string,
  timezone: string,
  settings: HistoryPageOptions,
): { entries: HistoryEntry[]; nextBefore: string | null } {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(query))
    throw new CliError(
      "E_TIME_FORMAT",
      "Expected YYYY-MM-DD",
      "thc day 2026-11-07",
      2,
    );
  const next = new Date(`${query}T00:00:00Z`);
  if (
    !Number.isFinite(next.valueOf()) ||
    next.toISOString().slice(0, 10) !== query
  )
    throw new CliError("E_TIME_FORMAT", "Invalid day", "thc day 2026-11-07", 2);
  next.setUTCDate(next.getUTCDate() + 1);
  return dayPage(
    reader,
    parseTime(query, timezone),
    parseTime(next.toISOString().slice(0, 10), timezone),
    settings,
  );
}
function aroundPage(
  reader: HistoryReader,
  at: string,
  limit: number,
  settings: HistoryPageOptions,
): { entries: HistoryEntry[]; nextBefore: string | null } {
  const result = reader.messagesAroundPage(at, limit, settings.before);
  const entries: HistoryEntry[] = [];
  let remaining = settings.maxChars ?? 8000;
  let nextBefore = result.nextBefore;
  for (const message of result.messages) {
    if (remaining <= 0) break;
    const chars = Array.from(message.text);
    const text = chars.slice(0, remaining).join("");
    remaining -= Array.from(text).length;
    entries.push({
      author: message.author,
      sentAt: message.sentAt,
      text,
      possiblyDuplicated: message.possiblyDuplicated,
      remainingCharacters: chars.length - Array.from(text).length,
    });
    nextBefore =
      entries.length < result.messages.length
        ? reader.messageCursor(message)
        : result.nextBefore;
  }
  return { entries, nextBefore };
}
function findEntries(
  reader: HistoryReader,
  query: string,
  args: Arguments,
  timezone: string,
  settings: HistoryPageOptions,
): { entries: HistoryEntry[]; nextBefore: string | null } {
  if (args.command === "search") return search(reader, query, settings);
  if (args.command === "day") return findDay(reader, query, timezone, settings);
  return aroundPage(
    reader,
    parseTime(query, timezone),
    positive(args.positionals[1], 25, 100),
    settings,
  );
}
function outputText(
  args: Arguments,
  query: string,
  timezone: string,
  directory: string,
  result: { entries: HistoryEntry[]; nextBefore: string | null },
  contexts: HistoryEntry[][],
  stats: { count: number; first: string | null; last: string | null },
): string {
  const { entries } = result;
  const nextBefore = result.nextBefore
    ? encodeCursor(result.nextBefore, directory)
    : null;
  if (args.flags.has("json"))
    return `${JSON.stringify({ entries: entries.map((item) => ({ ...item, time: formatTime(item.sentAt, timezone) })), contexts: contexts.map((group) => group.map((item) => ({ ...item, time: formatTime(item.sentAt, timezone) }))), nextBefore, ...(entries.length ? {} : { query, stats }) })}\n`;
  const text = entries.length
    ? `${entries.map((item, index) => [formatEntry(item, timezone), ...(contexts[index] ?? []).map((neighbor) => formatEntry(neighbor, timezone))].join("\n")).join("\n")}\n`
    : `No visible messages found for ${JSON.stringify(query.slice(0, 256))}. Searched ${String(stats.count)} messages (${stats.first ?? "empty"} to ${stats.last ?? "empty"}); try fewer words or --any.\n`;
  return nextBefore ? `${text}Continue: --after ${nextBefore}\n` : text;
}
function neighborEntries(
  reader: HistoryReader,
  hit: HistoryEntry,
  maxChars: number,
): HistoryEntry[] {
  const neighbors = around(reader, hit.sentAt, 3, maxChars);
  const index = neighbors.findIndex(
    (neighbor) =>
      neighbor.sentAt === hit.sentAt &&
      neighbor.author === hit.author &&
      (neighbor.text.startsWith(hit.text) ||
        hit.text.startsWith(neighbor.text)),
  );
  if (index >= 0) neighbors.splice(index, 1);
  return neighbors;
}
function boundedOutput(
  reader: HistoryReader,
  query: string,
  args: Arguments,
  timezone: string,
  directory: string,
): { output: string; count: number } {
  const settings = options(args, timezone, directory);
  const max = settings.maxChars ?? 8000;
  for (;;) {
    const result = findEntries(reader, query, args, timezone, settings);
    const contexts =
      args.command === "search"
        ? result.entries.map((hit) =>
            neighborEntries(
              reader,
              hit,
              Math.max(
                1,
                Math.floor(
                  (settings.maxChars ?? max) / (result.entries.length + 1),
                ),
              ),
            ),
          )
        : [];
    const output = outputText(
      args,
      query,
      timezone,
      directory,
      result,
      contexts,
      reader.historyStats(),
    );
    if (Array.from(output).length <= max)
      return { output, count: result.entries.length };
    const prior = settings.maxChars ?? max;
    if (prior <= 1) throw usage("--max-chars is too small to render a result");
    settings.maxChars = Math.max(1, Math.floor(prior / 2));
  }
}
export async function historyCommand(
  args: Arguments,
  deps: CliDependencies,
  directory: string,
  config: Config,
): Promise<void> {
  allowedFlags(args, [
    "any",
    "fts",
    "role",
    "since",
    "limit",
    "after",
    "max-chars",
    "full",
    "self-test",
  ]);
  if (args.flags.has("self-test")) {
    const reader = openHistory(join(directory, ".thc", "store.db"));
    reader.close();
    deps.stdout("History is readable.\n");
    return;
  }
  const query = args.positionals[0];
  if (!query) throw usage(`${args.command} requires an argument`);
  const reader = openHistory(join(directory, ".thc", "store.db"));
  let count = 0;
  let failure: string | null = null;
  try {
    const result = boundedOutput(
      reader,
      query,
      args,
      config.timezone,
      directory,
    );
    count = result.count;
    deps.stdout(result.output);
  } catch (error) {
    failure = error instanceof CliError ? error.info.code : "E_DEPENDENCY";
    throw error;
  } finally {
    reader.close();
    await usageLog(
      directory,
      args.command,
      args.command === "search" ? query.trim().split(/\s+/).length : 0,
      count,
      failure,
    );
  }
}
