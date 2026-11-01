export type Language = "ru" | "en";
export interface ChannelAttachment {
  kind: "photo" | "document" | "voice" | "audio" | "video";
  fileId: string;
  fileUniqueId: string;
  fileName?: string;
  fileSize?: number;
}
export interface EventBase {
  updateId: number;
  chatId: string;
  userId: string;
  messageId: number;
  sentAt: string;
  language: Language;
}
export type ChannelEvent =
  | (EventBase & {
      kind: "message";
      text: string;
      attachments: ChannelAttachment[];
      reply?: string;
      replyToMessageId?: number;
      replyToBot?: boolean;
      replySource?: { sentAt: string; text: string };
      replyAuthor?: string;
      replySentAt?: string;
      quote?: string;
      forward?: string;
      forwardAuthor?: string;
      forwardSentAt?: string;
      mediaGroupId?: string;
    })
  | (EventBase & { kind: "command"; command: string; arguments: string })
  | (EventBase & { kind: "decision"; callbackId: string; data: string })
  | (EventBase & { kind: "generation-stopped" })
  | (EventBase & { kind: "reaction"; reactions: string[] });
export interface ChannelPollResult {
  events: ChannelEvent[];
  nextOffset: number;
}
export interface TelegramChannelOptions {
  token: string;
  signal?: AbortSignal;
  allowedUserIds: readonly (string | number)[];
  apiRoot?: string;
  fetch?: typeof fetch;
  language?: Language;
  hiddenTags?: readonly string[];
}
export interface DeliveryResult {
  messageIds: number[];
  visibleText: string;
}
export interface SendOptions {
  replyToMessageId?: number;
  fallbackReplyToMessageId?: number;
  silent?: boolean;
}
