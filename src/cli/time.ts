import { CliError } from "./errors.js";

function parts(value: Date, timezone: string): Record<string, string> {
  const list = new Intl.DateTimeFormat("en-GB", {
    timeZone: timezone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
    timeZoneName: "short",
  }).formatToParts(value);
  return Object.fromEntries(list.map((item) => [item.type, item.value]));
}
export function formatTime(value: string, timezone: string): string {
  const p = parts(new Date(value), timezone);
  return `${p.year ?? ""}-${p.month ?? ""}-${p.day ?? ""} ${p.hour ?? ""}:${p.minute ?? ""}:${p.second ?? ""} ${p.timeZoneName ?? timezone}`;
}
function parseAbsolute(value: string): string | undefined {
  const relative = /^(\d+)d$/.exec(value);
  if (relative?.[1])
    return new Date(Date.now() - Number(relative[1]) * 86400000).toISOString();
  if (!/^\d{4}-\d{2}-\d{2}T/.test(value)) return;
  const time = new Date(value);
  if (!Number.isNaN(time.valueOf())) return time.toISOString();
}
export function parseTime(value: string, timezone: string): string {
  const absolute = parseAbsolute(value);
  if (absolute) return absolute;
  const match =
    /^(\d{4}-\d{2}-\d{2})(?:[ T](\d{2}:\d{2}:\d{2})(?:\s+(.+))?)?$/.exec(value);
  if (!match?.[1])
    throw new CliError(
      "E_TIME_FORMAT",
      "Expected YYYY-MM-DD or YYYY-MM-DD HH:MM:SS",
      'thc around "2026-11-07 18:00:00"',
      2,
    );
  const local = `${match[1]} ${match[2] ?? "00:00:00"}`;
  const target = Date.parse(`${local.replace(" ", "T")}Z`);
  if (!Number.isFinite(target))
    throw new CliError(
      "E_TIME_FORMAT",
      "Invalid date or time",
      "thc day 2026-11-07",
      2,
    );
  let utc = target;
  for (let i = 0; i < 3; i++) {
    const formatted = formatTime(new Date(utc).toISOString(), timezone);
    const wall = Date.parse(`${formatted.slice(0, 19).replace(" ", "T")}Z`);
    utc += target - wall;
  }
  const result = formatTime(new Date(utc).toISOString(), timezone);
  if (
    result.slice(0, 19) !== local ||
    (match[3] !== undefined && match[3] !== result.slice(20))
  )
    throw new CliError(
      "E_TIME_FORMAT",
      "Invalid time or timezone",
      'thc around "2026-11-07 18:00:00"',
      2,
    );
  return new Date(utc).toISOString();
}
