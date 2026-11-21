export type AdapterCapability =
  | "submit"
  | "observe"
  | "interrupt"
  | "approve"
  | "reconcile"
  | "newSession"
  | "attach"
  | "steer"
  | "compact"
  | "configure";
export interface HarnessSettings {
  model?: string;
  effort?: string;
  // A service tier id; null puts the thread back on the standard tier.
  serviceTier?: string | null;
}
export interface HarnessServiceTier {
  id: string;
  name: string;
  description: string;
}
export interface HarnessModel {
  id: string;
  model: string;
  displayName: string;
  description: string;
  hidden: boolean;
  isDefault: boolean;
  defaultReasoningEffort: string;
  supportedReasoningEfforts: { reasoningEffort: string; description: string }[];
  serviceTiers: HarnessServiceTier[];
}
export interface AdapterInput {
  correlationId: string;
  text: string;
  settings?: HarnessSettings;
}
export interface AdapterSession {
  id: string;
  cursor?: string;
}
export interface AdapterEvent {
  kind:
    | "input"
    | "output"
    | "turn-started"
    | "turn-completed"
    | "progress"
    | "approval-requested"
    | "question-requested"
    | "approval-resolved"
    | "turn-error"
    | "error";
  sessionId: string;
  cursor: string;
  turnId?: string;
  correlationId?: string;
  text?: string;
  requestId?: string;
  data?: unknown;
}
export interface AdapterAttachment {
  command: string;
  args: string[];
  env?: Record<string, string>;
}
export interface ApprovalDecision {
  requestId: string;
  decision: unknown;
}
export interface HarnessAdapter {
  readonly capabilities: ReadonlySet<AdapterCapability>;
  newSession(): Promise<AdapterSession>;
  submit(input: AdapterInput): Promise<{ turnId?: string }>;
  observe(): AsyncIterable<AdapterEvent>;
  interrupt(turnId: string): Promise<void>;
  approve(decision: ApprovalDecision): Promise<void>;
  reconcile(cursor?: string): Promise<AdapterEvent[]>;
  attach(): Promise<AdapterAttachment>;
  close(): Promise<void>;
  steer?(input: AdapterInput, turnId: string): Promise<void>;
  compact?(): Promise<void>;
  pendingState?(): Promise<"none" | "some" | "unknown">;
  listModels?(signal: AbortSignal): Promise<HarnessModel[]>;
  currentSettings?(signal: AbortSignal): Promise<HarnessSettings | undefined>;
}
export class UnsupportedCapabilityError extends Error {
  readonly capability: AdapterCapability;
  constructor(capability: AdapterCapability) {
    super(`Adapter does not support ${capability}`);
    this.name = "UnsupportedCapabilityError";
    this.capability = capability;
  }
}
export class SteerRejectedError extends Error {
  constructor() {
    super("The active turn ended before the follow-up was accepted");
    this.name = "SteerRejectedError";
  }
}
export class CompactRejectedError extends Error {
  constructor() {
    super("Compaction was rejected by the harness");
    this.name = "CompactRejectedError";
  }
}
export class SettingsUnsupportedError extends Error {
  constructor() {
    super("The harness cannot list models");
    this.name = "SettingsUnsupportedError";
  }
}
