import { randomUUID } from "node:crypto";
import { catalog } from "../channels/index.js";
import type { ChannelEvent, InlineButton } from "../channels/index.js";
import type { TurnInput } from "../store/index.js";
import type { RuntimeContext } from "./context.js";
import { buttonLabel, questionKey, questionRecord } from "./async-question.js";
import type { QuestionRecord } from "./async-question.js";

type DecisionEvent = Extract<ChannelEvent, { kind: "decision" }>;
interface Choice {
  key: string;
  raw: string;
  record: QuestionRecord;
  answer: string;
}
// `-` is the collapsed button that shows the chosen answer.
const pattern = /^aq:([0-9a-f]{16}):(\d{1,2}|-)$/u;

function read(raw: string | undefined): QuestionRecord | undefined {
  if (!raw) return undefined;
  try {
    return questionRecord.parse(JSON.parse(raw) as unknown);
  } catch {
    return undefined;
  }
}
// The message Codex's own client sends when the owner picks an option.
export function replyText(record: QuestionRecord, answer: string): string {
  const questionItemId = JSON.stringify([
    "request_user_input_async",
    record.itemId,
    record.index,
  ]);
  const body = JSON.stringify([
    { answer, question: record.title, questionItemId },
  ]);
  return `<send_user_message_question_reply>\n${body}\n</send_user_message_question_reply>`;
}
// The buttons sit under the last part of the question's message.
function buttonMessage(
  ctx: RuntimeContext,
  marker: string,
): number | null | undefined {
  let messageId: number | null | undefined;
  for (let part = 0; ; part++) {
    const row = ctx.store.getOutbox(`${marker}/${String(part)}`);
    if (!row) return messageId;
    messageId = row.messageId;
  }
}
function current(
  ctx: RuntimeContext,
  record: QuestionRecord,
  event: DecisionEvent,
): boolean {
  return (
    record.answer === undefined &&
    record.sessionId === ctx.sessionId &&
    record.chatId === event.chatId &&
    buttonMessage(ctx, record.marker) === event.messageId
  );
}
async function quietly(
  ctx: RuntimeContext,
  action: () => Promise<unknown>,
  failure: string,
): Promise<void> {
  try {
    await action();
  } catch {
    ctx.log(failure, { code: "unconfirmed" });
  }
}
// After an answer the buttons give way to one that shows it; a question from
// an earlier session loses its buttons.
function setButtons(
  ctx: RuntimeContext,
  event: DecisionEvent,
  rows: InlineButton[][],
): Promise<void> {
  return quietly(
    ctx,
    () =>
      ctx.channel.bot.api.editMessageReplyMarkup(
        event.chatId,
        event.messageId,
        { reply_markup: { inline_keyboard: rows } },
      ),
    "question.buttons-edit-failed",
  );
}
const answeredRow = (id: string, answer: string): InlineButton[][] => [
  [{ text: buttonLabel(`✓ ${answer}`), callback_data: `aq:${id}:-` }],
];
function commit(
  ctx: RuntimeContext,
  event: DecisionEvent,
  choice: Choice,
): boolean {
  const now = ctx.now().toISOString();
  const turn: TurnInput = {
    id: randomUUID(),
    entityId: ctx.entityId,
    source: "telegram",
    createdAt: now,
  };
  return ctx.store.answerQuestion({
    updateId: event.updateId,
    row: choice.key,
    expected: choice.raw,
    answered: JSON.stringify({ ...choice.record, answer: choice.answer }),
    turn,
    reply: replyText(choice.record, choice.answer),
    history: {
      id: `question-answer/${String(event.updateId)}`,
      chatId: event.chatId,
      author: "user",
      sentAt: now,
      text: choice.answer,
      possiblyDuplicated: false,
    },
  });
}
async function stale(
  ctx: RuntimeContext,
  event: DecisionEvent,
  id: string,
  record: QuestionRecord | undefined,
): Promise<true> {
  await quietly(
    ctx,
    () =>
      ctx.channel.answerCallback(
        event.callbackId,
        catalog[event.language].stale,
      ),
    "callback_ack_failed",
  );
  // The collapsed button already shows the answer.
  if (event.data.endsWith(":-")) return true;
  if (record?.answer !== undefined)
    await setButtons(ctx, event, answeredRow(id, record.answer));
  else if (record && record.sessionId !== ctx.sessionId)
    await setButtons(ctx, event, []);
  return true;
}
// A tap on an option answers Codex's question. The answer becomes a turn of
// its own, which the engine steers into the running task whatever the
// follow-up mode, or submits as the next task once nothing runs. A question
// is answered once; a second tap, or a tap after /new, is stale.
export async function answerQuestion(
  ctx: RuntimeContext,
  event: DecisionEvent,
): Promise<boolean> {
  const match = pattern.exec(event.data);
  const id = match?.[1];
  if (!id || !match[2]) return false;
  const key = questionKey(id);
  const raw = ctx.store.getCursor(key);
  const record = read(raw);
  const answer =
    match[2] === "-" ? undefined : record?.options[Number(match[2])];
  if (
    !raw ||
    !record ||
    answer === undefined ||
    !current(ctx, record, event) ||
    !commit(ctx, event, { key, raw, record, answer })
  )
    return stale(ctx, event, id, record);
  await quietly(
    ctx,
    () => ctx.channel.answerCallback(event.callbackId),
    "callback_ack_failed",
  );
  await setButtons(ctx, event, answeredRow(id, answer));
  return true;
}
