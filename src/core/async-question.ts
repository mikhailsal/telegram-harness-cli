import { createHash } from "node:crypto";
import { z } from "zod";
import type { AdapterEvent } from "../adapters/contract.js";
import type { InlineButton } from "../channels/types.js";
import type { Turn } from "../store/index.js";
import type { RuntimeContext } from "./context.js";
import { messageFormat, turnChatId } from "./format.js";
import { enqueueMidTurn } from "./mid-turn.js";

// A longer list than this stays text, as Codex wrote it.
const maxOptions = 10;
// A button shows about this many characters on a phone; the message lists
// longer options in full, and a button label stops at `maxLabel`.
const shortOption = 40;
const maxLabel = 64;
const asyncSchema = z.object({ delivery: z.literal("async") }).loose();
const itemSchema = z
  .object({
    itemId: z.string().min(1),
    questions: z
      .array(
        z
          .object({
            title: z.string(),
            options: z.array(z.string()).nullish(),
          })
          .loose(),
      )
      .min(1),
  })
  .loose();
export const questionRecord = z.strictObject({
  sessionId: z.string(),
  chatId: z.string(),
  turnId: z.string(),
  itemId: z.string(),
  /** The outbox rows of the question's message share this ID prefix. */
  marker: z.string(),
  index: z.number().int().nonnegative(),
  title: z.string(),
  options: z.array(z.string()),
  answer: z.string().optional(),
});
export type QuestionRecord = z.infer<typeof questionRecord>;
export const questionKey = (id: string): string => `async-question/${id}`;

export function buttonLabel(text: string): string {
  const chars = Array.from(text.trim());
  return chars.length > maxLabel
    ? `${chars.slice(0, maxLabel - 1).join("")}…`
    : chars.join("");
}
export function isAsyncOutput(event: AdapterEvent): boolean {
  return event.kind === "output" && asyncSchema.safeParse(event.data).success;
}
function buttons(
  id: string,
  options: readonly string[],
): InlineButton[][] | undefined {
  if (
    !options.length ||
    options.length > maxOptions ||
    options.some((option) => !option.trim())
  )
    return undefined;
  return options.map((option, index) => [
    { text: buttonLabel(option), callback_data: `aq:${id}:${String(index)}` },
  ]);
}
interface Prepared {
  text: string;
  keyboard?: InlineButton[][];
}
type ItemQuestion = z.infer<typeof itemSchema>["questions"][number];
function prepare(id: string, question: ItemQuestion): Prepared {
  const title = question.title.trim();
  const options = question.options ?? [];
  const keyboard = title ? buttons(id, options) : undefined;
  const short = options.every(
    (option) => Array.from(option).length <= shortOption,
  );
  if (keyboard && short) return { text: title, keyboard };
  const lines = [title, ...options.map((option) => `- ${option}`)];
  const text = lines.filter(Boolean).join("\n");
  return keyboard ? { text, keyboard } : { text };
}
interface Target {
  ctx: RuntimeContext;
  turn: Turn;
  itemId: string;
  base: {
    chatId: string;
    author: "assistant";
    silent: false;
    format: ReturnType<typeof messageFormat>;
  };
}
function queueQuestion(
  target: Target,
  marker: string,
  index: number,
  question: ItemQuestion,
): void {
  const { ctx, turn, base } = target;
  const part = `${marker}/q${String(index)}`;
  const id = createHash("sha256").update(part).digest("hex").slice(0, 16);
  const message = prepare(id, question);
  if (!message.text) return;
  if (message.keyboard && !ctx.store.getCursor(questionKey(id))) {
    const record: QuestionRecord = {
      sessionId: ctx.sessionId,
      chatId: base.chatId,
      turnId: turn.id,
      itemId: target.itemId,
      marker: part,
      index,
      title: question.title.trim(),
      options: question.options ?? [],
    };
    ctx.store.setCursor(questionKey(id), JSON.stringify(record));
  }
  enqueueMidTurn(ctx, turn, { ...base, marker: part, ...message });
}
// Codex asks the owner through request_user_input_async: the question arrives
// mid-turn as its own agent message, sent at once and never the turn's final
// answer. Each question of the item becomes a message whose options are
// buttons; a tap answers it (see question-answer.ts), and so does a typed
// message. A finished turn's question is stale: replayed history must not
// resend it.
export function queueAsyncOutput(
  ctx: RuntimeContext,
  turn: Turn,
  event: AdapterEvent,
): void {
  if (!["submitting", "active", "uncertain"].includes(turn.status)) return;
  const marker = `async/${turn.id}/${event.cursor}`;
  if (ctx.store.getCursor(marker)) return;
  const chatId = turnChatId(ctx, turn);
  const base = {
    chatId,
    author: "assistant" as const,
    silent: false as const,
    format: messageFormat(ctx, turn, chatId),
  };
  const item = itemSchema.safeParse(event.data);
  if (!item.success) {
    if (event.text?.trim())
      enqueueMidTurn(ctx, turn, { ...base, marker, text: event.text });
    return;
  }
  const target = { ctx, turn, base, itemId: item.data.itemId };
  for (const [index, question] of item.data.questions.entries())
    queueQuestion(target, marker, index, question);
  ctx.store.setCursor(marker, "1");
}
