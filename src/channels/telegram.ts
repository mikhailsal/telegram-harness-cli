import { Bot, GrammyError, InputFile } from "grammy";
import { catalog } from "./catalog.js";
import { parseUpdate } from "./ingress.js";
import { renderOutgoing } from "./render.js";
import { LocalMediaError, mediaBytes } from "./render-media.js";
import { DraftLedger } from "./draft-ledger.js";
import type { RenderChunk } from "./render.js";
import type {
  ChannelPollResult,
  DeliveryResult,
  SendOptions,
  TelegramChannelOptions,
  TelegramMessageFormat,
} from "./types.js";

export type TelegramErrorKind =
  "bad-request" | "forbidden" | "rate-limit" | "network" | "server" | "other";
export function classifyTelegramError(error: unknown): {
  kind: TelegramErrorKind;
  retryAfter?: number;
} {
  if (error instanceof LocalMediaError) return { kind: "other" };
  if (!(error instanceof GrammyError)) return { kind: "network" };
  if (error.error_code === 429)
    return {
      kind: "rate-limit",
      retryAfter: error.parameters.retry_after ?? 1,
    };
  if (error.error_code === 400) return { kind: "bad-request" };
  if (error.error_code === 403) return { kind: "forbidden" };
  if (error.error_code >= 500) return { kind: "server" };
  return { kind: "other" };
}
function isLocalMedia(chunk: RenderChunk): boolean {
  return chunk.kind === "photo" || chunk.kind === "document";
}
export class TelegramChannel {
  readonly bot: Bot;
  private readonly allowed: Set<string>;
  private readonly options: TelegramChannelOptions;
  private readonly drafts = new DraftLedger();
  constructor(options: TelegramChannelOptions) {
    this.options = options;
    this.allowed = new Set(options.allowedUserIds.map(String));
    this.bot = new Bot(options.token, {
      client: {
        ...(options.apiRoot ? { apiRoot: options.apiRoot } : {}),
        ...(options.fetch ? { fetch: options.fetch } : {}),
      },
    });
    this.bot.api.config.use(async (prev, method, payload, signal) => {
      const deadline = AbortSignal.timeout(
        method === "getUpdates" ? 55000 : 30000,
      );
      const signals = [
        deadline,
        ...(options.signal ? [options.signal] : []),
        ...(signal ? [signal as unknown as AbortSignal] : []),
      ];
      // grammY ships a structurally incompatible AbortSignal declaration; Node supplies the runtime signal.
      const result = await prev(
        method,
        payload,
        AbortSignal.any(signals) as unknown as typeof signal,
      );
      if (result.ok) this.drafts.track(method, payload);
      return result;
    });
  }
  hasDraft(
    chatId: string,
    draftId: number,
    format?: TelegramMessageFormat,
  ): boolean {
    return this.drafts.has(chatId, draftId, format);
  }
  downloadSignal(): AbortSignal {
    const signals = [
      AbortSignal.timeout(30000),
      ...(this.options.signal ? [this.options.signal] : []),
    ];
    return AbortSignal.any(signals);
  }
  async poll(offset: number, signal?: AbortSignal): Promise<ChannelPollResult> {
    const updates = await this.bot.api.getUpdates(
      {
        offset,
        timeout: 50,
        allowed_updates: [
          "message",
          "callback_query",
          "message_reaction",
          "stopped_message_generation",
        ],
      },
      signal as unknown as Parameters<typeof this.bot.api.getUpdates>[1],
    );
    const events: ChannelPollResult["events"] = [];
    let nextOffset = offset;
    for (const update of updates) {
      nextOffset = Math.max(nextOffset, update.update_id + 1);
      const parsed = parseUpdate(
        update,
        this.allowed,
        this.options.language ?? "en",
        this.options.token.split(":", 1)[0],
      );
      if (parsed.event) events.push(parsed.event);
      if (parsed.stranger) {
        void this.bot.api
          .sendMessage(parsed.stranger.chatId, String(parsed.stranger.userId), {
            disable_notification: true,
          })
          .catch(() => undefined);
      }
    }
    return { events, nextOffset };
  }
  async send(
    chatId: string,
    markdown: string,
    options: SendOptions = {},
  ): Promise<DeliveryResult> {
    const rendered = renderOutgoing(
      markdown,
      this.options.hiddenTags,
      undefined,
      options.format,
    );
    const chunks = rendered.chunks.length
      ? rendered.chunks
      : [
          {
            kind: "message" as const,
            text: catalog[this.options.language ?? "en"].empty,
            plain: catalog[this.options.language ?? "en"].empty,
            ...(options.format === "regular" ? { style: "html" as const } : {}),
          },
        ];
    const ids: number[] = [],
      texts: string[] = [];
    for (const chunk of chunks) {
      const silent =
        options.silent === true || ids.length > 0 || chunk.kind === "file";
      const extra = {
        disable_notification: silent,
        ...(options.replyToMessageId && ids.length === 0
          ? { reply_parameters: { message_id: options.replyToMessageId } }
          : {}),
      };
      const delivered = await this.sendChunk(chatId, chunk, extra);
      ids.push(delivered.id);
      texts.push(delivered.text);
    }
    return { messageIds: ids, visibleText: texts.join("\n\n") };
  }
  async deliverChunk(
    chatId: string,
    chunk: RenderChunk,
    options: SendOptions = {},
  ): Promise<DeliveryResult> {
    const extra = {
      disable_notification: options.silent === true || chunk.kind === "file",
      ...(options.replyToMessageId
        ? { reply_parameters: { message_id: options.replyToMessageId } }
        : {}),
    };
    const delivered = await this.sendChunk(
      chatId,
      chunk,
      extra,
      options.fallbackReplyToMessageId,
    );
    return { messageIds: [delivered.id], visibleText: delivered.text };
  }
  private async sendChunk(
    chatId: string,
    chunk: RenderChunk,
    extra: {
      disable_notification: boolean;
      reply_parameters?: { message_id: number };
    },
    fallbackReplyToMessageId?: number,
  ): Promise<{ id: number; text: string }> {
    if (isLocalMedia(chunk)) return this.sendLocalMedia(chatId, chunk, extra);
    if (chunk.kind === "file") {
      const message = await this.bot.api.sendDocument(
        chatId,
        new InputFile(Buffer.from(chunk.text), "response.md"),
        extra,
      );
      return { id: message.message_id, text: chunk.text };
    }
    if (chunk.style === "html") return this.sendHtml(chatId, chunk, extra);
    try {
      const message = await this.bot.api.sendRichMessage(
        chatId,
        { markdown: chunk.text },
        extra,
      );
      return { id: message.message_id, text: chunk.plain };
    } catch (error) {
      if (classifyTelegramError(error).kind !== "bad-request") throw error;
      const fallback = {
        ...extra,
        ...(fallbackReplyToMessageId === undefined
          ? {}
          : { reply_parameters: { message_id: fallbackReplyToMessageId } }),
      };
      try {
        if (chunk.plain.length > 4096) throw error;
        const message = await this.bot.api.sendMessage(
          chatId,
          chunk.plain,
          fallback,
        );
        return { id: message.message_id, text: chunk.plain };
      } catch (plainError) {
        if (classifyTelegramError(plainError).kind !== "bad-request")
          throw plainError;
        const message = await this.bot.api.sendDocument(
          chatId,
          new InputFile(Buffer.from(chunk.text), "response.md"),
          { ...fallback, disable_notification: true },
        );
        return { id: message.message_id, text: chunk.text };
      }
    }
  }
  private async sendHtml(
    chatId: string,
    chunk: RenderChunk,
    extra: {
      disable_notification: boolean;
      reply_parameters?: { message_id: number };
    },
  ): Promise<{ id: number; text: string }> {
    try {
      const message = await this.bot.api.sendMessage(chatId, chunk.text, {
        ...extra,
        parse_mode: "HTML",
      });
      return { id: message.message_id, text: chunk.plain };
    } catch (error) {
      if (classifyTelegramError(error).kind !== "bad-request") throw error;
      const message = await this.bot.api.sendMessage(
        chatId,
        chunk.plain,
        extra,
      );
      return { id: message.message_id, text: chunk.plain };
    }
  }
  private async sendLocalMedia(
    chatId: string,
    chunk: RenderChunk,
    extra: {
      disable_notification: boolean;
      reply_parameters?: { message_id: number };
    },
  ): Promise<{ id: number; text: string }> {
    if (!chunk.workspace || !chunk.filePath || !chunk.fileName || !chunk.digest)
      throw new LocalMediaError("invalid-local-file");
    const bytes = mediaBytes(chunk.workspace, {
      kind: chunk.kind === "photo" ? "photo" : "document",
      filePath: chunk.filePath,
      fileName: chunk.fileName,
      digest: chunk.digest,
    });
    const caption = Array.from(chunk.plain).slice(0, 1024).join("");
    const mediaOptions = caption ? { ...extra, caption } : extra;
    if (chunk.kind === "photo") {
      try {
        const message = await this.bot.api.sendPhoto(
          chatId,
          new InputFile(bytes, chunk.fileName),
          mediaOptions,
        );
        return { id: message.message_id, text: caption };
      } catch (error) {
        if (classifyTelegramError(error).kind !== "bad-request") throw error;
      }
    }
    const message = await this.bot.api.sendDocument(
      chatId,
      new InputFile(bytes, chunk.fileName),
      mediaOptions,
    );
    return { id: message.message_id, text: caption };
  }
  async edit(chatId: string, messageId: number, text: string): Promise<void> {
    await this.bot.api.editMessageText(chatId, messageId, text);
  }
  async delete(chatId: string, messageId: number): Promise<void> {
    await this.bot.api.deleteMessage(chatId, messageId);
  }
  async answerCallback(id: string, text?: string): Promise<void> {
    await this.bot.api.answerCallbackQuery(id, text ? { text } : {});
  }
  async typing(chatId: string): Promise<void> {
    await this.bot.api.sendChatAction(chatId, "typing");
  }
  async draft(
    chatId: number,
    draftId: number,
    text: string,
    format: TelegramMessageFormat = "rich",
  ): Promise<void> {
    if (format === "regular") {
      await this.bot.api.sendMessageDraft(chatId, draftId, text, {
        can_stop: true,
      });
      return;
    }
    // Match rich final delivery so Telegram consumes the preview immediately.
    await this.bot.api.sendRichMessageDraft(
      chatId,
      draftId,
      { blocks: [{ type: "thinking", text }] },
      { can_stop: true },
    );
  }
  async finalizeDraft(
    chatId: number,
    draftId: number,
    text: string,
    format: TelegramMessageFormat,
  ): Promise<number> {
    if (!Number.isSafeInteger(draftId) || draftId <= 0)
      throw new Error("Invalid draft ID");
    if (format === "regular")
      return (
        await this.bot.api.sendMessage(chatId, text, {
          disable_notification: true,
        })
      ).message_id;
    return (
      await this.bot.api.sendRichMessage(
        chatId,
        { blocks: [{ type: "paragraph", text }] },
        { disable_notification: true },
      )
    ).message_id;
  }
  async menu(): Promise<void> {
    for (const scope of [undefined, { type: "all_private_chats" as const }])
      for (const language of [undefined, "ru", "en"] as const) {
        const strings = catalog[language ?? "en"];
        await this.bot.api.setMyCommands(
          (
            [
              "stop",
              "status",
              "new",
              "mode",
              "format",
              "compact",
              "help",
            ] as const
          ).map((command) => ({ command, description: strings[command] })),
          {
            ...(language ? { language_code: language } : {}),
            ...(scope ? { scope } : {}),
          },
        );
      }
  }
}
