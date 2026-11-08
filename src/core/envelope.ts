import type { ChannelEvent } from "../channels/index.js";
export type MessageEvent = Extract<ChannelEvent, { kind: "message" }>;

function formatTimestamp(value: string, timezone: string): string {
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: timezone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
    timeZoneName: "short",
  }).formatToParts(new Date(value));
  const item = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${item.year ?? ""}-${item.month ?? ""}-${item.day ?? ""} ${item.hour ?? ""}:${item.minute ?? ""}:${item.second ?? ""} ${item.timeZoneName ?? timezone}`;
}
function contextLine(
  label: string,
  author: string | undefined,
  sentAt: string | undefined,
  timezone: string,
  text?: string,
): string {
  const details = [
    author,
    sentAt ? formatTimestamp(sentAt, timezone) : undefined,
  ]
    .filter(Boolean)
    .join(" · ");
  return `${label}${details ? ` (${details})` : ""}${text ? `: ${text}` : ""}`;
}
export function envelope(
  events: MessageEvent[],
  paths: string[],
  reaction?: string,
  timezone = "UTC",
): string {
  const first = events[0];
  if (!first) throw new Error("A message is required");
  const lines = [`[${formatTimestamp(first.sentAt, timezone)}]`];
  const reply = replyContext(first, timezone);
  if (reply !== undefined) lines.push(reply);
  if (first.replySource)
    lines.push(
      contextLine(
        "Original request",
        undefined,
        first.replySource.sentAt,
        timezone,
        first.replySource.text,
      ),
    );
  if (first.forward)
    lines.push(
      contextLine(
        "Forwarded",
        first.forwardAuthor,
        first.forwardSentAt,
        timezone,
      ),
    );
  if (reaction) lines.push(`Reactions: ${reaction}`);
  for (const event of events) if (event.text) lines.push(event.text);
  lines.push(...paths);
  return lines.join("\n");
}
function replyContext(
  first: MessageEvent,
  timezone: string,
): string | undefined {
  if (
    first.reply !== undefined ||
    first.quote !== undefined ||
    first.replyAuthor ||
    first.replySentAt
  )
    return contextLine(
      first.quote !== undefined ? "Quote" : "Reply",
      first.replyAuthor,
      first.replySentAt,
      timezone,
      first.quote ?? first.reply,
    );
}
