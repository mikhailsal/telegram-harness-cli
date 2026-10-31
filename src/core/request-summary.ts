import { catalog } from "../channels/index.js";
import type { PendingRequest } from "../store/index.js";
import type { RuntimeContext } from "./context.js";
import { readAnswers } from "./pending-question.js";

type Language = "ru" | "en";
function summaryLabel(request: PendingRequest, language: Language): string {
  const strings = catalog[language];
  if (request.status === "resolved_elsewhere") return strings.resolvedElsewhere;
  if (request.status === "expired") return strings.expired;
  if (request.kind === "question")
    return language === "ru" ? "Ты ответил" : "You answered";
  return request.decision === "accept" ? strings.allowed : strings.denied;
}
function questions(value: unknown): string {
  if (!Array.isArray(value)) return "";
  return value
    .flatMap((item: unknown) =>
      item &&
      typeof item === "object" &&
      "question" in item &&
      typeof item.question === "string"
        ? [item.question]
        : [],
    )
    .join("; ");
}
function details(
  ctx: RuntimeContext,
  request: PendingRequest,
  raw: Record<string, unknown>,
): string {
  let text =
    request.kind === "question"
      ? questions(raw.questions)
      : typeof raw.detail === "string"
        ? raw.detail
        : "";
  if (
    request.kind === "approval" &&
    /^(?:(?:call|item|request|tool)[-_][\w-]+|[0-9a-f]{8}-[0-9a-f-]{27,})$/iu.test(
      text,
    )
  )
    text = "";
  if (request.kind !== "question" || request.status !== "answered") return text;
  const answers = Object.values(readAnswers(ctx.store, request.id))
    .flatMap((value) => value.answers)
    .join(", ");
  return [text, answers].filter(Boolean).join(" — ");
}
function resolvedAt(ctx: RuntimeContext, request: PendingRequest): string {
  const stored = ctx.store.getCursor(`request-resolved-at/${request.id}`);
  return stored && Number.isFinite(Date.parse(stored))
    ? stored
    : ctx.now().toISOString();
}
function recordSummary(ctx: RuntimeContext, request: PendingRequest): void {
  if (ctx.store.getCursor(`request-history/${request.id}`)) return;
  const value: unknown = JSON.parse(request.payload);
  if (
    !value ||
    typeof value !== "object" ||
    !("chatId" in value) ||
    typeof value.chatId !== "string"
  )
    return;
  const raw = value as Record<string, unknown>;
  const language: Language = raw.language === "ru" ? "ru" : "en";
  const date = resolvedAt(ctx, request);
  const time = new Intl.DateTimeFormat(language === "ru" ? "ru-RU" : "en-GB", {
    timeZone: ctx.config.timezone,
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(new Date(date));
  const subject = details(ctx, request, raw)
    .replace(/[\p{Cc}\p{Cf}]+/gu, " ")
    .trim();
  const text = `${summaryLabel(request, language)}${subject ? `: ${subject}` : ""} · ${time}`;
  ctx.store.recordDecisionHistory({
    id: `request-summary/${request.id}`,
    chatId: value.chatId,
    author: "system",
    sentAt: date,
    text,
    turnId: request.turnId,
    possiblyDuplicated: false,
  });
}
export function queueRequestSummaries(ctx: RuntimeContext): void {
  for (const request of ctx.store.listRequests([
    "answered",
    "resolved_elsewhere",
    "expired",
  ]))
    recordSummary(ctx, request);
  for (const request of ctx.store.listRequests(["stale", "cancelled"]))
    ctx.store.discardLegacyDecisionNotice(`request-summary/${request.id}`);
}
