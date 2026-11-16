import { SettingsUnsupportedError } from "../adapters/contract.js";
import { fill } from "../channels/index.js";
import type { Language } from "../channels/index.js";
import type { RuntimeContext } from "./context.js";
import { desiredCursor, desiredSettings } from "./settings-audit.js";
import type { SettingsState } from "./harness-settings-model.js";

// Runs an action inside the engine's serial queue (see EngineDelivery).
export type Serialize = (action: () => Promise<void>) => Promise<void>;
export type Outcome<T> = { ok: true; value: T } | { ok: false; error: unknown };
export const readBudgetMs = 10_000;
const running = new WeakMap<RuntimeContext, Set<Promise<void>>>();

// Settings reads are RPCs that can take seconds; they run outside the engine
// queue so /stop and buttons stay responsive, then re-enter it to commit.
export function later<T>(
  ctx: RuntimeContext,
  serialize: Serialize | undefined,
  read: (signal: AbortSignal) => Promise<T>,
  commit: (outcome: Outcome<T>) => Promise<void>,
): void {
  const task = (async () => {
    const signal = AbortSignal.any([
      ctx.signal,
      AbortSignal.timeout(readBudgetMs),
    ]);
    let outcome: Outcome<T>;
    try {
      outcome = { ok: true, value: await read(signal) };
    } catch (error) {
      outcome = { ok: false, error };
    }
    const run = async () => {
      try {
        await commit(outcome);
      } catch {
        ctx.log("settings.commit-failed");
      }
    };
    // serialize also pumps the queue afterwards, which can throw on its own.
    await (serialize ? serialize(run) : run()).catch(() => {
      ctx.log("settings.pump-failed");
    });
  })();
  const tasks = running.get(ctx) ?? new Set();
  running.set(ctx, tasks);
  tasks.add(task);
  void task.finally(() => tasks.delete(task));
}

export async function settingsIdle(ctx: RuntimeContext): Promise<void> {
  for (;;) {
    const tasks = [...(running.get(ctx) ?? [])];
    if (!tasks.length) return;
    await Promise.allSettled(tasks);
  }
}

// The reason a read failed, in the reply's words.
export function failureText(
  ctx: RuntimeContext,
  language: Language,
  error: unknown,
): string {
  if (error instanceof SettingsUnsupportedError)
    return fill(language, "settingsUnsupported");
  const code =
    error && typeof error === "object" && "code" in error
      ? error.code
      : undefined;
  const message = error instanceof Error ? error.message : "";
  const reason =
    typeof code === "number"
      ? `RPC ${String(code)}`
      : /abort|timed out|timeout/iu.test(message)
        ? "timeout"
        : "error";
  ctx.log("settings.list-failed", { code: reason });
  return fill(language, "settingsUnavailable", { reason });
}

// Not-ready and tmux replies need no RPC.
export function precondition(
  ctx: RuntimeContext,
  language: Language,
): string | undefined {
  const adapter = ctx.adapter;
  if (!adapter.capabilities.has("configure") || !adapter.listModels)
    return fill(language, "settingsNeedProtocol");
  if (!ctx.adapterReady) return fill(language, "settingsNotReady");
  return undefined;
}

export async function readState(
  ctx: RuntimeContext,
  signal: AbortSignal,
  needModels = true,
): Promise<SettingsState> {
  const adapter = ctx.adapter;
  const [models, current] = await Promise.all([
    needModels && adapter.listModels
      ? adapter.listModels(signal)
      : Promise.resolve([]),
    readRunning(ctx, signal),
  ]);
  return { models, running: current, desired: desiredSettings(ctx) };
}

export async function readRunning(
  ctx: RuntimeContext,
  signal: AbortSignal,
): Promise<SettingsState["running"]> {
  if (!ctx.adapter.currentSettings) return undefined;
  try {
    return await ctx.adapter.currentSettings(signal);
  } catch {
    return undefined;
  }
}

export function desiredVersion(ctx: RuntimeContext): string {
  return ctx.store.getCursor(desiredCursor) ?? "";
}

export const holdCursor = "settings-blocked";
// Queued turns wait while the model Codex rejected is still the one desired.
export function settingsHeld(ctx: RuntimeContext): boolean {
  const blocked = ctx.store.getCursor(holdCursor);
  return Boolean(blocked) && desiredSettings(ctx)?.model === blocked;
}
export function releaseHold(ctx: RuntimeContext): void {
  if (ctx.store.getCursor(holdCursor)) ctx.store.setCursor(holdCursor, "");
}
