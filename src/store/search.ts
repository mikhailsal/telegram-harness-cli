import type { SearchOptions } from "./types.js";

function escapeLike(value: string): string {
  return value.replace(/[\\%_]/g, "\\$&");
}
function termCondition(term: string, values: string[]): string {
  if (Array.from(term).length < 3) {
    values.push(`%${escapeLike(term)}%`);
    return "m.text LIKE ? ESCAPE '\\'";
  }
  values.push(`"${term.replace(/"/g, '""')}"`);
  return "m.rowid IN (SELECT rowid FROM messages_fts WHERE messages_fts MATCH ?)";
}
export function searchQuery(
  query: string,
  options: SearchOptions,
): { sql: string; values: string[] } {
  const values: string[] = [];
  const conditions: string[] = [];
  if (!query.trim()) throw new Error("Search query must not be empty");
  if (options.raw) {
    conditions.push(
      "m.rowid IN (SELECT rowid FROM messages_fts WHERE messages_fts MATCH ?)",
    );
    values.push(query);
  } else {
    const terms = query
      .trim()
      .split(/\s+/u)
      .map((term) => termCondition(term, values));
    conditions.push(`(${terms.join(options.any ? " OR " : " AND ")})`);
  }
  if (options.role) {
    conditions.push("m.author=?");
    values.push(options.role);
  }
  if (options.since) {
    conditions.push("m.sentAt>=?");
    values.push(options.since);
  }
  return {
    sql: `SELECT m.* FROM messages m WHERE ${conditions.join(" AND ")}`,
    values,
  };
}
