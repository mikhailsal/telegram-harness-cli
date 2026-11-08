import type { TelegramMessageFormat } from "./types.js";
interface Draft {
  id: number;
  expiresAt: number;
  format: TelegramMessageFormat;
}
function family(method: string): TelegramMessageFormat | undefined {
  if (method === "sendRichMessageDraft" || method === "sendRichMessage")
    return "rich";
  if (method === "sendMessageDraft" || method === "sendMessage")
    return "regular";
  return undefined;
}
export class DraftLedger {
  private readonly rows = new Map<string, Draft>();
  track(method: string, payload: unknown): void {
    if (!payload || typeof payload !== "object" || !("chat_id" in payload))
      return;
    const chatId = String(payload.chat_id),
      format = family(method);
    if (!format) return;
    if (method.endsWith("Draft") && "draft_id" in payload)
      this.rows.set(chatId, {
        id: Number(payload.draft_id),
        expiresAt: Date.now() + 30000,
        format,
      });
    else if (
      !method.endsWith("Draft") &&
      this.rows.get(chatId)?.format === format
    )
      this.rows.delete(chatId);
  }
  has(
    chatId: string,
    draftId: number,
    format?: TelegramMessageFormat,
  ): boolean {
    const draft = this.rows.get(chatId);
    return (
      draft?.id === draftId &&
      draft.expiresAt > Date.now() &&
      (format === undefined || draft.format === format)
    );
  }
}
