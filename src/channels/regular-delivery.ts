import { GrammyError } from "grammy";
import type { Bot } from "grammy";
import type { RenderChunk } from "./render.js";

type Api = Bot["api"];
type Chunk = Pick<RenderChunk, "text" | "plain">;
interface Options {
  chatId: string;
  extra: {
    disable_notification: boolean;
    reply_parameters?: { message_id: number };
  };
  consumeDraft?: boolean;
  existingMessageId?: number;
  onMessageCreated?: (id: number) => void;
}
function badRequest(error: unknown): error is GrammyError {
  return error instanceof GrammyError && error.error_code === 400;
}
function notModified(error: unknown): boolean {
  return (
    badRequest(error) && /message is not modified/iu.test(error.description)
  );
}
async function sendPlain(
  api: Api,
  chunk: Chunk,
  options: Options,
): Promise<number> {
  try {
    return (
      await api.sendMessage(options.chatId, chunk.text, {
        ...options.extra,
        parse_mode: "HTML",
      })
    ).message_id;
  } catch (error) {
    if (!badRequest(error)) throw error;
    return (await api.sendMessage(options.chatId, chunk.plain, options.extra))
      .message_id;
  }
}
async function sendRich(
  api: Api,
  chunk: Chunk,
  options: Options,
): Promise<number> {
  try {
    return (
      await api.sendRichMessage(
        options.chatId,
        { html: chunk.text },
        options.extra,
      )
    ).message_id;
  } catch (error) {
    if (!badRequest(error)) throw error;
    return (
      await api.sendRichMessage(
        options.chatId,
        { blocks: [{ type: "paragraph", text: chunk.plain }] },
        options.extra,
      )
    ).message_id;
  }
}
async function editRegular(
  api: Api,
  chatId: string,
  id: number,
  chunk: Chunk,
): Promise<void> {
  try {
    await api.editMessageText(chatId, id, chunk.text, { parse_mode: "HTML" });
  } catch (error) {
    if (notModified(error)) return;
    if (!badRequest(error)) throw error;
    try {
      await api.editMessageText(chatId, id, chunk.plain);
    } catch (fallbackError) {
      if (!notModified(fallbackError)) throw fallbackError;
    }
  }
}
export async function sendRegular(
  api: Api,
  chunk: Chunk,
  options: Options,
): Promise<{ id: number; text: string }> {
  if (options.existingMessageId !== undefined) {
    await editRegular(api, options.chatId, options.existingMessageId, chunk);
    return { id: options.existingMessageId, text: chunk.plain };
  }
  if (!options.consumeDraft)
    return { id: await sendPlain(api, chunk, options), text: chunk.plain };
  const id = await sendRich(api, chunk, options);
  options.onMessageCreated?.(id);
  await editRegular(api, options.chatId, id, chunk);
  return { id, text: chunk.plain };
}
