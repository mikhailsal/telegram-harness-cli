import { z } from "zod";
import type { AdapterEvent } from "../adapters/contract.ts";
import type { Store, PendingRequest } from "../store/index.ts";
import type { RuntimeContext } from "./context.ts";

const questionSchema = z
  .object({
    id: z.string().min(1),
    question: z.string(),
    isSecret: z.boolean().default(false),
    options: z
      .array(z.object({ label: z.string(), description: z.string() }))
      .nullable()
      .optional(),
  })
  .loose();
const requestSchema = z
  .object({ questions: z.array(questionSchema).min(1) })
  .loose();
const progressSchema = z.record(
  z.string(),
  z.object({ answers: z.array(z.string()).min(1) }),
);
export type Question = z.infer<typeof questionSchema>;
export type Answers = z.infer<typeof progressSchema>;
export function readQuestions(event: AdapterEvent): Question[] {
  const questions = requestSchema.parse(event.data).questions;
  if (questions.some((question) => question.isSecret))
    throw new Error("Secret questions cannot be relayed safely");
  return questions;
}
export function readAnswers(store: Store, requestId: string): Answers {
  const raw = store.getCursor(`question/${requestId}`);
  return raw ? progressSchema.parse(JSON.parse(raw) as unknown) : {};
}
export function nextQuestion(
  questions: Question[],
  answers: Answers,
): Question | undefined {
  return questions.find((question) => answers[question.id] === undefined);
}

const payloadSchema = z.strictObject({
  sessionId: z.string(),
  requestId: z.string(),
  chatId: z.string(),
  userId: z.string(),
  language: z.enum(["ru", "en"]),
  externalTurnId: z.string(),
  detail: z.string(),
  summary: z.string(),
  questions: z.array(
    z
      .object({
        id: z.string(),
        question: z.string(),
        isSecret: z.boolean(),
        options: z
          .array(z.object({ label: z.string(), description: z.string() }))
          .nullable()
          .optional(),
      })
      .loose(),
  ),
});
export type Payload = z.infer<typeof payloadSchema>;
export function parsePayload(row: PendingRequest): Payload {
  return payloadSchema.parse(JSON.parse(row.payload) as unknown);
}
export function detail(event: AdapterEvent): string {
  const value = z
    .object({
      command: z.string().optional(),
      reason: z.string().optional(),
      itemId: z.string().optional(),
    })
    .loose()
    .safeParse(event.data);
  if (!value.success) return "Approval requested";
  return (
    value.data.command ??
    value.data.reason ??
    value.data.itemId ??
    "Approval requested"
  );
}
export function live(row: PendingRequest, ctx: RuntimeContext): boolean {
  const turn = ctx.store.getTurn(row.turnId);
  return (
    turn !== undefined &&
    ["submitting", "active", "uncertain"].includes(turn.status) &&
    (!turn.externalTurnId ||
      turn.externalTurnId === parsePayload(row).externalTurnId)
  );
}

export function mayDecide(
  ctx: RuntimeContext,
  row: PendingRequest,
  event: { userId: string; chatId: string; messageId: number },
  data: Payload,
): boolean {
  return (
    data.userId === event.userId &&
    data.chatId === event.chatId &&
    live(row, ctx) &&
    ctx.store
      .listTransients()
      .some(
        (card) =>
          card.id === `request/${row.id}` && card.messageId === event.messageId,
      )
  );
}
export function selectedQuestion(
  row: PendingRequest,
  data: Payload,
  action: string,
  store: Store,
): string | undefined {
  if (row.kind !== "question") return;
  const match = /^q(\d+)-(\d+)$/u.exec(action);
  if (!match) return;
  const current = nextQuestion(data.questions, readAnswers(store, row.id));
  const selected = data.questions[Number(match[1])];
  if (!current || selected?.id !== current.id) return;
  return selected.options?.[Number(match[2])]?.label;
}

export async function sendQuestionCard(
  ctx: RuntimeContext,
  chatId: string,
  requestId: string,
  index: number,
  question: Question,
  silent: boolean,
): Promise<number> {
  const message = await ctx.channel.bot.api.sendMessage(
    chatId,
    question.question,
    {
      disable_notification: silent,
      reply_markup: {
        inline_keyboard: (question.options ?? []).map((option, optionIndex) => [
          {
            text: option.label,
            callback_data: `p:${requestId}:q${String(index)}-${String(optionIndex)}`,
          },
        ]),
      },
    },
  );
  return message.message_id;
}
