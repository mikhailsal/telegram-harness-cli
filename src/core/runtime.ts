import { randomUUID } from "node:crypto";
import { execFile } from "node:child_process";
import { readFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import {
  AttachmentManager,
  TelegramChannel,
  TransientManager,
} from "../channels/index.js";
import { openStore, type Store } from "../store/index.js";
import { openAdapter, createFreshAdapter, persistAdapter } from "./adapter.js";
import {
  loadConfig,
  parseConfig,
  resolveToken,
  type Config,
} from "./config.js";
import type { RuntimeContext } from "./context.js";
import { Delivery } from "./delivery.js";
import { DownNotice } from "./down-notice.js";
import { TurnEngine } from "./engine.js";
import { AlreadyRunningError, acquireDirectoryLock } from "./lock.js";
import { runtimeLog } from "./log.js";
import { registerMenus } from "./runtime-menu.js";
import { pollLoop, maintenanceLoop } from "./runtime-poll.js";
import { offlineAdapter, stopped } from "./runtime-util.js";
import {
  backupBeforeMigration,
  notifyStartupFailure,
  type StartupOptions,
} from "./startup.js";
import { Supervisor, type AdapterFactory } from "./supervisor.js";
import { refreshUpdateCache } from "./update.js";

export { notifyStartupFailure } from "./startup.js";
export interface RuntimeOptions extends StartupOptions {
  signal?: AbortSignal;
  channel?: TelegramChannel;
  adapterFactory?: AdapterFactory;
  freshAdapterFactory?: AdapterFactory;
  now?: () => Date;
  log?: (event: string, details?: Record<string, unknown>) => void;
  startDaemon?: () => Promise<void>;
  tickIntervalMs?: number;
}
const execute = promisify(execFile);
function noticeFor(directory: string, channel: TelegramChannel): DownNotice {
  return new DownNotice(directory, {
    send: async (chatId, text) => {
      const id = (await channel.send(chatId, text)).messageIds[0];
      if (id === undefined) throw new Error("Notice returned no message ID");
      return id;
    },
    delete: (chatId, id) => channel.delete(chatId, id),
  });
}
function entity(
  store: Store,
  directory: string,
  token: string,
  now: Date,
): string {
  const existing = store.getCursor("entity-id");
  if (existing) {
    const saved = store.getEntity(existing);
    const botId = token.split(":", 1)[0] ?? "";
    if (saved?.directory !== directory || saved.botId !== botId)
      throw new Error("Store belongs to another folder or bot");
    return existing;
  }
  const id = randomUUID();
  store.putEntity({
    id,
    directory,
    botId: token.split(":", 1)[0] ?? "",
    createdAt: now.toISOString(),
  });
  store.setCursor("entity-id", id);
  return id;
}
async function packageVersion(): Promise<string | undefined> {
  try {
    const root = fileURLToPath(new URL("../../package.json", import.meta.url));
    const value: unknown = JSON.parse(await readFile(root, "utf8"));
    if (
      typeof value === "object" &&
      value !== null &&
      "version" in value &&
      typeof value.version === "string"
    )
      return value.version;
  } catch {
    return;
  }
}
function backgroundUpdate(
  directory: string,
  config: Config,
  options: RuntimeOptions,
  signal: AbortSignal,
): void {
  if (!config.update_check) return;
  void packageVersion()
    .then(async (version) => {
      if (!version || signal.aborted) return;
      const fetcher = options.fetch ?? fetch;
      const guarded: typeof fetch = (input, init) =>
        fetcher(input, {
          ...init,
          signal: AbortSignal.any([
            signal,
            ...(init?.signal ? [init.signal] : []),
          ]),
        });
      await refreshUpdateCache(directory, version, {
        enabled: true,
        fetch: guarded,
      });
    })
    .catch(() => undefined);
}
function makeContext(
  directory: string,
  config: Config,
  token: string,
  store: Store,
  signal: AbortSignal,
  options: RuntimeOptions,
): RuntimeContext {
  const now = options.now ?? (() => new Date());
  const channel =
    options.channel ??
    new TelegramChannel({
      token,
      allowedUserIds: config.allowlist,
      language: config.language === "auto" ? "en" : config.language,
      hiddenTags: config.markup.tags.map((tag) => tag.name),
      signal,
      ...(options.apiRoot ? { apiRoot: options.apiRoot } : {}),
      ...(options.fetch ? { fetch: options.fetch } : {}),
    });
  const notice = noticeFor(directory, channel);
  const entityId = entity(store, directory, token, now());
  const logger = runtimeLog(directory);
  const ctx: RuntimeContext = {
    directory,
    config,
    store,
    signal,
    channel,
    notice,
    now,
    entityId,
    sessionId: store.getCursor("active-session") ?? "",
    adapter: offlineAdapter(),
    adapterReady: false,
    transient: new TransientManager(channel, store, now),
    files: new AttachmentManager(
      channel,
      store,
      join(directory, ".thc"),
      token,
      options.apiRoot,
      options.fetch,
    ),
    log: (event, details) => {
      logger.write(event, details);
      options.log?.(event, details);
    },
    persistAdapter: () => {
      const session = store.getSession(ctx.sessionId);
      persistAdapter(ctx.adapter, store, session?.externalId);
    },
    rotateSession: () =>
      Promise.reject(new Error("Runtime supervisor is not initialized")),
  };
  runtimeLoggers.set(ctx, logger.flush);
  return ctx;
}
const runtimeLoggers = new WeakMap<RuntimeContext, () => Promise<void>>();
function daemonStarter(
  ctx: RuntimeContext,
  options: RuntimeOptions,
): () => Promise<void> {
  if (options.startDaemon) return options.startDaemon;
  return async () => {
    await execute(
      ctx.config.codex.executable,
      ["app-server", "daemon", "start"],
      { cwd: ctx.directory, timeout: 30000, maxBuffer: 1024 * 1024 },
    );
  };
}
async function serve(
  ctx: RuntimeContext,
  options: RuntimeOptions,
  controller: AbortController,
): Promise<void> {
  const delivery = new Delivery(ctx);
  const engine = new TurnEngine(ctx, delivery);
  const supervisor = new Supervisor(ctx, engine, {
    factory:
      options.adapterFactory ??
      ((context) =>
        openAdapter(context.directory, context.config, context.store)),
    freshFactory:
      options.freshAdapterFactory ??
      options.adapterFactory ??
      ((context) =>
        createFreshAdapter(context.directory, context.config, context.store)),
    startDaemon: daemonStarter(ctx, options),
  });
  ctx.rotateSession = () => supervisor.rotate();
  backgroundUpdate(ctx.directory, ctx.config, options, ctx.signal);
  const interval = Math.max(10, options.tickIntervalMs ?? 1000);
  void registerMenus(ctx);
  const tasks = [
    pollLoop(ctx, engine),
    supervisor.run(),
    maintenanceLoop(ctx, engine, interval),
  ];
  try {
    await Promise.all(tasks);
  } catch (error) {
    controller.abort();
    await Promise.allSettled(tasks);
    throw error;
  } finally {
    await ctx.adapter.close().catch(() => undefined);
    try {
      await engine.close();
    } finally {
      await runtimeLoggers.get(ctx)?.();
    }
  }
}
function signals(
  controller: AbortController,
  external?: AbortSignal,
): () => void {
  const stop = (): void => {
    controller.abort();
  };
  process.once("SIGINT", stop);
  process.once("SIGTERM", stop);
  external?.addEventListener("abort", stop, { once: true });
  if (external?.aborted) stop();
  return () => {
    process.removeListener("SIGINT", stop);
    process.removeListener("SIGTERM", stop);
    external?.removeEventListener("abort", stop);
  };
}
async function configured(root: string, supplied?: Config): Promise<Config> {
  return supplied ? parseConfig(supplied) : loadConfig(root);
}
async function restoreLanguage(ctx: RuntimeContext): Promise<void> {
  const language = await ctx.notice.language();
  if (language && !ctx.store.getCursor("language"))
    ctx.store.setCursor("language", language);
}
async function runWithStore(
  root: string,
  config: Config,
  token: string,
  controller: AbortController,
  options: RuntimeOptions,
): Promise<void> {
  const store = openStore(join(root, ".thc", "store.db"));
  try {
    store.recoverSending();
    const ctx = makeContext(
      root,
      config,
      token,
      store,
      controller.signal,
      options,
    );
    await restoreLanguage(ctx);
    await serve(ctx, options, controller);
  } finally {
    store.close();
  }
}
export async function startBridge(
  directory: string,
  config?: Config,
  options: RuntimeOptions = {},
): Promise<void> {
  const root = resolve(directory);
  const controller = new AbortController();
  const removeSignals = signals(controller, options.signal);
  let lost: Error | undefined;
  try {
    if (stopped(controller.signal)) return;
    const checked = await configured(root, config);
    const token = await resolveToken(checked.token, root);
    const lease = await acquireDirectoryLock(root);
    void lease.lost.then((error) => {
      lost = error;
      controller.abort();
    });
    try {
      if (stopped(controller.signal)) return;
      await backupBeforeMigration(root);
      if (stopped(controller.signal)) return;
      await runWithStore(root, checked, token, controller, options);
      if (lost) throw lost;
    } finally {
      await lease.release();
    }
  } catch (error) {
    if (!(error instanceof AlreadyRunningError) && !controller.signal.aborted)
      await notifyStartupFailure(root, error, options).catch(() => undefined);
    throw error;
  } finally {
    controller.abort();
    removeSignals();
  }
}
