export interface Entity {
  id: string;
  directory: string;
  botId: string;
  createdAt: string;
}
export interface HarnessSession {
  id: string;
  entityId: string;
  harness: string;
  externalId: string;
  startedAt: string;
  endedAt: string | null;
}
export interface InboxInput {
  updateId: number;
  entityId: string;
  chatId: string;
  userId: string;
  receivedAt: string;
  payload: string;
}
export type Inbox = InboxInput & {
  status: "queued" | "assigned" | "consumed";
  turnId: string | null;
};
export type TurnStatus =
  | "queued"
  | "submitting"
  | "active"
  | "uncertain"
  | "completed"
  | "failed"
  | "cancelled"
  | "interrupted";
export interface TurnInput {
  id: string;
  entityId: string;
  source: "telegram" | "terminal" | "external";
  createdAt: string;
}
export type Turn = TurnInput & {
  status: TurnStatus;
  sessionId: string | null;
  externalTurnId: string | null;
  updatedAt: string;
  result: string | null;
};
export interface TurnPatch {
  sessionId?: string | null;
  externalTurnId?: string | null;
  updatedAt?: string;
  result?: string | null;
}
export type RequestStatus =
  | "pending"
  | "answering"
  | "answered"
  | "resolved_elsewhere"
  | "stale"
  | "expired"
  | "cancelled";
export interface PendingRequestInput {
  id: string;
  turnId: string;
  kind: "approval" | "question";
  payload: string;
  createdAt: string;
  expiresAt?: string | null;
}
export type PendingRequest = PendingRequestInput & {
  status: RequestStatus;
  decision: string | null;
  expiresAt: string | null;
};
export type OutboxStatus = "pending" | "sending" | "sent" | "failed";
export interface OutboxInput {
  id: string;
  turnId: string;
  chunk: number;
  chatId: string;
  text: string;
  createdAt: string;
  format?: "rich" | "plain" | "document";
  fileName?: string | null;
  payload?: string | null;
  replyToMessageId?: number | null;
  disableNotification?: boolean;
}
export type Outbox = OutboxInput & {
  status: OutboxStatus;
  messageId: number | null;
  error: string | null;
  possiblyDuplicated: boolean;
  retryUsed: boolean;
  format: "rich" | "plain" | "document";
  fileName: string | null;
  payload: string | null;
  replyToMessageId: number | null;
  disableNotification: boolean;
  attempts: number;
  nextAttemptAt: string | null;
};
export interface TransientInput {
  id: string;
  kind: "status" | "approval" | "question" | "welcome" | "command";
  chatId: string;
  createdAt: string;
  expiresAt: string | null;
}
export type Transient = TransientInput & {
  messageId: number | null;
  sentAt: string | null;
};
export interface AttachmentInput {
  updateId: number;
  fileUniqueId: string;
  fileKey: string;
  path: string;
  createdAt: string;
  turnId: string | null;
}
export type Attachment = AttachmentInput;
export interface VisibleMessageInput {
  id: string;
  chatId: string;
  telegramMessageId: number;
  author: "user" | "assistant" | "system";
  sentAt: string;
  text: string;
  turnId: string | null;
  possiblyDuplicated: boolean;
}
export type VisibleMessage = VisibleMessageInput;
export interface SearchOptions {
  any?: boolean;
  raw?: boolean;
  role?: "user" | "assistant" | "system";
  since?: string;
}
export interface MessagePage {
  messages: VisibleMessage[];
  nextBefore: string | null;
}
