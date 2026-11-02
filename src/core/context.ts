import type { HarnessAdapter } from "../adapters/contract.js";
import type {
  AttachmentManager,
  TelegramChannel,
  TransientManager,
} from "../channels/index.js";
import type { Store } from "../store/index.js";
import type { Config } from "./config.js";
import type { DownNotice } from "./down-notice.js";

export interface RuntimeContext {
  directory: string;
  config: Config;
  store: Store;
  adapter: HarnessAdapter;
  adapterReady: boolean;
  channel: TelegramChannel;
  entityId: string;
  sessionId: string;
  transient: TransientManager;
  files: AttachmentManager;
  notice: DownNotice;
  signal: AbortSignal;
  now: () => Date;
  log: (event: string, details?: Record<string, unknown>) => void;
  persistAdapter: () => void;
  rotateSession: (updateId?: number) => Promise<void>;
}
