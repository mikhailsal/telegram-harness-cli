import { join } from "node:path";
import { z } from "zod";
import { StateFile } from "./state.ts";

const stateSchema = z.strictObject({
  language: z.enum(["ru", "en"]).optional(),
  notice: z
    .strictObject({
      key: z.string(),
      chatId: z.string(),
      messageId: z.number().optional(),
    })
    .optional(),
});
type NoticeState = z.infer<typeof stateSchema>;
export interface NoticeApi {
  send: (chatId: string, text: string) => Promise<number>;
  delete: (chatId: string, messageId: number) => Promise<void>;
}

export class DownNotice {
  private readonly state: StateFile<NoticeState>;
  private readonly api: NoticeApi;
  constructor(directory: string, api: NoticeApi) {
    this.state = new StateFile(
      join(directory, ".thc", "down-notice.json"),
      stateSchema,
      {},
    );
    this.api = api;
  }
  async language(): Promise<"ru" | "en" | undefined> {
    return (await this.state.read()).language;
  }
  async rememberLanguage(language: string): Promise<void> {
    await this.state.update((current) => ({
      ...current,
      language: language.startsWith("ru") ? "ru" : "en",
    }));
  }
  async show(key: string, chatId: string, text: string): Promise<void> {
    const status: { send: boolean; retained: boolean } = {
      send: false,
      retained: false,
    };
    await this.state.update((current) => {
      if (current.notice?.key === key) return current;
      status.send = true;
      return { ...current, notice: { key, chatId } };
    });
    if (!status.send) return;
    const messageId = await this.api.send(chatId, text);
    await this.state.update((current) => {
      if (current.notice?.key !== key) return current;
      status.retained = true;
      return { ...current, notice: { key, chatId, messageId } };
    });
    if (!status.retained) await this.api.delete(chatId, messageId);
  }
  async healthy(): Promise<void> {
    const notice = (await this.state.read()).notice;
    if (notice?.messageId !== undefined)
      await this.api.delete(notice.chatId, notice.messageId);
    await this.state.update((current) => {
      if (
        current.notice?.key !== notice?.key ||
        current.notice?.messageId !== notice?.messageId
      )
        return current;
      return { ...(current.language ? { language: current.language } : {}) };
    });
  }
}
