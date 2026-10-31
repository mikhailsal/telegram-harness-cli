import { catalog, transientIntent } from "../channels/index.js";
import type { RuntimeContext } from "./context.js";
import type { TurnEngine } from "./engine.js";
import { delay, stopped, untilAbort } from "./runtime-util.js";

function conflict(error: unknown): boolean {
  return (
    error instanceof Error && "error_code" in error && error.error_code === 409
  );
}
async function notifyConflict(ctx: RuntimeContext): Promise<void> {
  ctx.store.setCursor("poll-conflict", "1");
  ctx.log("telegram.conflict");
  const language = ctx.store.getCursor("language") === "ru" ? "ru" : "en";
  const chat =
    ctx.store.getCursor("chat-id") ?? String(ctx.config.allowlist[0]);
  await ctx.notice
    .show("poll-conflict", chat, catalog[language].pollConflict)
    .catch(() => undefined);
}
async function acceptPoll(
  ctx: RuntimeContext,
  engine: TurnEngine,
): Promise<boolean> {
  const offset = Number(ctx.store.getCursor("telegram-offset") ?? "0");
  const signal = AbortSignal.any([ctx.signal, AbortSignal.timeout(55000)]);
  const result = await untilAbort(ctx.channel.poll(offset, signal), ctx.signal);
  if (!result) return false;
  for (const event of result.events)
    await ctx.notice.rememberLanguage(event.language);
  await engine.accept(result.events, result.nextOffset);
  ctx.store.setCursor("poll-conflict", "0");
  if (ctx.adapterReady) await ctx.notice.healthy().catch(() => undefined);
  return result.events.length > 0;
}
export async function pollLoop(
  ctx: RuntimeContext,
  engine: TurnEngine,
): Promise<void> {
  let wait = 1000;
  while (!ctx.signal.aborted) {
    try {
      const gotEvents = await acceptPoll(ctx, engine);
      wait = 1000;
      if (!gotEvents) await delay(100, ctx.signal);
    } catch (error) {
      if (stopped(ctx.signal)) return;
      if (conflict(error)) await notifyConflict(ctx);
      else ctx.log("telegram.poll-failed");
      await delay(wait, ctx.signal);
      wait = Math.min(wait * 2, 60000);
    }
  }
}
export async function sendWelcome(ctx: RuntimeContext): Promise<void> {
  if (stopped(ctx.signal)) return;
  const pending = ctx.store.getCursor("welcome-pending");
  if (!pending) return;
  const delivered = ctx.store.getCursor("first-delivery-at");
  if (delivered && delivered >= pending) {
    ctx.store.setCursor("welcome-pending", "");
    return;
  }
  const language = ctx.store.getCursor("language") === "ru" ? "ru" : "en";
  const chat =
    ctx.store.getCursor("chat-id") ?? String(ctx.config.allowlist[0]);
  const intent = transientIntent(
    `welcome/${pending}`,
    "welcome",
    chat,
    ctx.now(),
  );
  try {
    await ctx.transient.send(intent, catalog[language].welcomePrompt);
  } catch {
    ctx.log("welcome.send-failed");
  }
  if (
    !stopped(ctx.signal) &&
    !ctx.store.getCursor(`transient-retry/${intent.id}`)
  )
    ctx.store.setCursor("welcome-pending", "");
}
export async function maintenanceLoop(
  ctx: RuntimeContext,
  engine: TurnEngine,
  interval: number,
): Promise<void> {
  let retentionAt = 0;
  while (!ctx.signal.aborted) {
    try {
      await engine.tick();
      if (ctx.adapterReady) await sendWelcome(ctx);
      const now = ctx.now().getTime();
      if (now >= retentionAt) {
        await ctx.files.retainSince(
          new Date(now - ctx.config.files_keep_days * 86400000),
        );
        retentionAt = now + 3600000;
      }
    } catch {
      ctx.log("maintenance.failed");
    }
    await delay(interval, ctx.signal);
  }
}
